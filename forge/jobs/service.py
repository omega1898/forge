"""JobService: the controller-side dispatcher.

It never holds a database transaction while talking to a host. Each step
reads committed state, talks to the host, then records what happened
through the controller as the trusted system actor:

    submit()   authorize, then record the job and its outbox entry (one commit)
    pump()     send pending outbox entries; record receipts
    poll()     relay host events into the record (fenced by attempt + epoch)
    recover()  after a restart: pump + poll; the host ledger deduplicates

Lost attempts are handled by retry class:
    safe_to_repeat          -> new attempt with a new lease epoch
    reconcile_before_retry  -> retry only if the target confirms the effect
                               is absent; otherwise reconciliation required
    never_automatic         -> reconciliation required (a person decides)
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta
from typing import Any

from forge.authority.authorize import (
    ActionRequest,
    ControllerPolicy,
    HostPolicy,
    ResourceLimits,
    authorize_action,
)
from forge.contracts.clock import Clock, SystemClock, format_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.jobs import TERMINAL_JOB_STATUSES, EffectClass, JobSpec, RetryClass
from forge.contracts.versions import SCHEMA_VERSION
from forge.hostagent.agent import HostUnreachable


class JobService:
    def __init__(
        self,
        controller,  # noqa: ANN001 - forge.controller.service.Controller
        hosts: dict[str, Any],
        *,
        host_policies: dict[str, HostPolicy],
        controller_policy: ControllerPolicy,
        clock: Clock | None = None,
        request_lifetime: timedelta = timedelta(hours=1),
    ) -> None:
        self.controller = controller
        self.hosts = hosts
        self.host_policies = host_policies
        self.controller_policy = controller_policy
        self.clock = clock or controller.clock or SystemClock()
        self.request_lifetime = request_lifetime
        self.fenced_reports = 0

    @property
    def store(self):  # noqa: ANN201
        return self.controller.store

    def _send(self, method: str, params: dict[str, Any]) -> Any:
        return self.controller.apply_command(
            {"schema_version": SCHEMA_VERSION, "method": method, "project_id": self.store.project().project_id,
             "idempotency_key": new_id("request"), "params": params},
            self.controller.system_actor,
        )

    # --------------------------------------------------------------- submit

    def submit(
        self,
        task_id: str,
        snapshot_id: str,
        *,
        host_id: str,
        arguments: dict[str, Any],
        effect_class: EffectClass,
        retry_class: RetryClass,
        grant_ids: list[str],
        limits: ResourceLimits,
        tool_digest: str | None = None,
        required_capabilities: list[str] | None = None,
    ) -> str:
        project = self.store.project()
        snapshot = self.store.snapshot(snapshot_id)
        if snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        policy = self.host_policies.get(host_id)
        if policy is None or host_id not in self.hosts:
            raise ForgeError(ErrorCode.MISSING_CAPABILITY, f"host {host_id} is not enrolled")
        decision = authorize_action(
            project_id=project.project_id,
            request=ActionRequest(action="run_registered_tool", limits=limits,
                                  required_capabilities=required_capabilities or []),
            grant_ids=grant_ids,
            trust_store=self.controller.trust_store,
            host_policy=policy,
            controller_policy=self.controller_policy,
            now=self.clock.now(),
        )
        if not decision.allowed:
            raise ForgeError(
                ErrorCode.PERMISSION_DENIED,
                f"job not authorized: {', '.join(decision.reasons)}",
                details={"reasons": decision.reasons, "details": decision.details},
            )
        spec = self._spec(
            job_id=new_id("job"), attempt=1, epoch=1, task_id=task_id, host_id=host_id,
            snapshot_digest=snapshot.manifest_digest, arguments=arguments, effect_class=effect_class,
            retry_class=retry_class, grant_ids=decision.used_grant_ids, grant_digests=decision.grant_digests,
            policy_digest=decision.policy_digest, limits=limits, tool_digest=tool_digest,
        )
        self._send("job.create", {"spec": spec.model_dump(mode="json"), "job_digest": spec.digest()})
        return spec.job_id

    def _spec(self, *, job_id: str, attempt: int, epoch: int, task_id: str, host_id: str, snapshot_digest: str,
              arguments: dict[str, Any], effect_class: str, retry_class: str, grant_ids: list[str],
              grant_digests: list[str], policy_digest: str, limits: ResourceLimits,
              tool_digest: str | None) -> JobSpec:
        now: datetime = self.clock.now()
        return JobSpec(
            job_id=job_id, attempt_number=attempt, lease_epoch=epoch,
            project_id=self.store.project().project_id, task_id=task_id,
            controller_id=self.controller.controller_id, host_id=host_id,
            snapshot_digest=snapshot_digest, tool_ref=str(arguments.get("tool_id") or arguments.get("kind")),
            tool_digest=tool_digest, runtime_digest=None, arguments=arguments,
            grant_ids=grant_ids, grant_digests=grant_digests, policy_digest=policy_digest,
            resource_budget=limits, effect_class=effect_class, retry_class=retry_class,
            request_nonce=secrets.token_hex(16), issued_at=format_timestamp(now),
            expires_at=format_timestamp(now + self.request_lifetime),
        )

    # ----------------------------------------------------------------- pump

    def pump(self) -> int:
        """Send every pending outbox entry the host can be reached for."""
        sent = 0
        for entry in self.store.pending_outbox():
            job = self.store.job(entry["job_id"], entry["attempt_number"])
            host = self.hosts.get(job.host_id)
            if host is None:
                continue
            if entry["kind"] == "submit":
                if job.cancel_requested_at is not None:
                    # Cancelled before it was ever sent: never send it.
                    self._send("job.receipt", {"job_id": job.job_id, "attempt_number": job.attempt_number,
                                               "lease_epoch": job.lease_epoch, "accepted": False,
                                               "reason": "cancelled before dispatch"})
                    continue
                revoked = self._revoked_grants(job)
                if revoked:
                    # Authority is rechecked before dispatch: the job never starts.
                    self._send("job.receipt", {"job_id": job.job_id, "attempt_number": job.attempt_number,
                                               "lease_epoch": job.lease_epoch, "accepted": False,
                                               "reason": f"grant no longer effective: {', '.join(revoked)}"})
                    continue
                try:
                    receipt = host.submit_job(job.spec, job.job_digest)
                except HostUnreachable:
                    continue  # stays pending; the host ledger makes a resend safe
                self._send("job.receipt", {"job_id": job.job_id, "attempt_number": job.attempt_number,
                                           "lease_epoch": job.lease_epoch, "accepted": receipt.accepted,
                                           "reason": receipt.reason})
            else:
                if job.execution_status not in ("cancelled",):
                    try:
                        host.cancel_job(job.job_id, job.attempt_number, job.lease_epoch, "cancel requested")
                    except HostUnreachable:
                        continue
                self._send("job.receipt", {"kind": "cancel", "job_id": job.job_id,
                                           "attempt_number": job.attempt_number, "lease_epoch": job.lease_epoch,
                                           "accepted": True})
            sent += 1
        return sent

    def _revoked_grants(self, job) -> list[str]:  # noqa: ANN001
        trust = self.controller.trust_store
        grant_ids = job.spec.get("grant_ids", [])
        if trust is None:
            return list(grant_ids)
        return [g for g in grant_ids if trust.effective_grant(g, job.project_id) is None]

    # ----------------------------------------------------------------- poll

    def poll(self) -> None:
        for job in self.store.jobs(active_only=True):
            host = self.hosts.get(job.host_id)
            if host is None or job.execution_status == "queued":
                continue
            try:
                events = host.read_job_events(job.job_id, job.attempt_number, job.host_event_seq)
            except HostUnreachable:
                continue
            for event in events:
                try:
                    self._send("job.observe", event.model_dump(mode="json"))
                except ForgeError as error:
                    if error.details.get("reason") == "fenced":
                        self.fenced_reports += 1  # late report from a superseded attempt
                        continue
                    raise
        for job in self.store.jobs():
            latest = self.store.job(job.job_id)
            if latest.attempt_number == job.attempt_number and job.execution_status == "lost" \
                    and job.reconciliation is None:
                self._handle_lost(job)
        self._finish_cancellations()

    def _handle_lost(self, job) -> None:  # noqa: ANN001
        spec = JobSpec.model_validate(job.spec)
        retry = self._spec(
            job_id=job.job_id, attempt=job.attempt_number + 1, epoch=job.lease_epoch + 1,
            task_id=job.task_id, host_id=job.host_id, snapshot_digest=spec.snapshot_digest,
            arguments=dict(spec.arguments), effect_class=spec.effect_class, retry_class=spec.retry_class,
            grant_ids=list(spec.grant_ids), grant_digests=list(spec.grant_digests),
            policy_digest=spec.policy_digest, limits=spec.resource_budget, tool_digest=spec.tool_digest,
        )
        try:
            self._send("job.retry", {"previous_attempt": job.attempt_number, "spec": retry.model_dump(mode="json"),
                                     "job_digest": retry.digest(), "reason": "attempt lost"})
        except ForgeError as error:
            if error.code not in (ErrorCode.PERMISSION_DENIED, ErrorCode.INVALID_REQUEST):
                raise
            self._send("job.reconciliation_required", {"job_id": job.job_id, "attempt_number": job.attempt_number,
                                                       "reason": error.message[:900]})

    def _finish_cancellations(self) -> None:
        for task in self.store.iter_tasks(status="running"):
            jobs = self.store.jobs(task_id=task.task_id)
            if jobs and any(j.cancel_requested_at for j in jobs) and all(
                j.execution_status in TERMINAL_JOB_STATUSES for j in jobs
            ):
                # Only now, with every job's terminal state observed on the
                # host, is the task recorded as cancelled.
                self.controller.apply_command(
                    {"schema_version": SCHEMA_VERSION, "method": "task.transition",
                     "project_id": task.project_id, "idempotency_key": new_id("request"),
                     "expected_record_version": task.record_version,
                     "params": {"task_id": task.task_id, "to_status": "cancelled",
                                "reason": "cancellation observed on the host"}},
                    self.controller.system_actor,
                )

    # --------------------------------------------------------------- helpers

    def recover(self) -> None:
        """After a controller restart: resend, then reconcile from host ledgers."""
        self.pump()
        self.poll()

    def cancel_task(self, task_id: str, reason: str, actor=None) -> Any:  # noqa: ANN001
        receipt = self.controller.apply_command(
            {"schema_version": SCHEMA_VERSION, "method": "task.cancel", "project_id": self.store.project().project_id,
             "idempotency_key": new_id("request"), "params": {"task_id": task_id, "reason": reason}},
            actor or self.controller.system_actor,
        )
        self.pump()
        return receipt

    def settle(self, job_id: str, *, timeout: float = 60.0) -> Any:
        """Pump and poll until the job's latest attempt is terminal and handled."""
        deadline = time.monotonic() + timeout
        while True:
            self.pump()
            self.poll()
            job = self.store.job(job_id)
            done = job.execution_status in TERMINAL_JOB_STATUSES and (
                job.execution_status != "lost" or job.reconciliation is not None
            )
            if done or time.monotonic() > deadline:
                return job
            time.sleep(0.02)
