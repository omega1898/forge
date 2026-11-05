"""A local execution host with its own durable ledger.

The host is a separate trust domain from the controller, even when both run
on one machine: it re-checks every request against its own policy, records
each request durably *before* executing it, and answers repeated deliveries
from its ledger instead of running work twice.

Submission order (blueprint Part 4.3): check size and protocol; verify the
recipient, freshness and the digest of the immutable spec; consult the
duplicate ledger; check the host's own ceiling; record a queued receipt;
only then start execution.

Fencing: the ledger keys work by (job_id, attempt_number). The same attempt
with a different spec is a protocol error; an older lease epoch is
rejected, so an old controller instance cannot restart superseded work.

External effects record an intent row before acting and are reconciled
after a restart by asking the target (ExternalEffectAdapter.lookup); they
are never repeated automatically.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal, Protocol

from forge.artifacts.blobs import BlobStore
from forge.authority.authorize import ResourceLimits
from forge.contracts.clock import Clock, SystemClock, format_timestamp, parse_timestamp
from forge.contracts.digests import canonical_json_bytes
from forge.contracts.jobs import TERMINAL_JOB_STATUSES, HostJobEvent, JobReceipt, JobSpec
from forge.execution.collector import collect_outputs
from forge.sandbox.base import SandboxBackend, SandboxSpec
from forge.tools.registry import ToolRegistry
from forge.workspace.snapshot import load_manifest, materialize

MAX_SPEC_BYTES = 256 * 1024


class HostUnreachable(Exception):
    """The controller could not reach the host (it may or may not have acted)."""


class SimulatedCrash(Exception):
    """Test hook: the host process 'dies' at a named point."""


class ExternalEffectAdapter(Protocol):
    def apply(self, operation_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def lookup(self, operation_id: str) -> Literal["applied", "absent", "unknown"]: ...


_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    job_id TEXT NOT NULL, attempt_number INTEGER NOT NULL, lease_epoch INTEGER NOT NULL,
    spec_digest TEXT NOT NULL, spec_json TEXT NOT NULL, state TEXT NOT NULL,
    intent_json TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, last_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS events (
    job_id TEXT NOT NULL, attempt_number INTEGER NOT NULL, lease_epoch INTEGER NOT NULL,
    sequence INTEGER NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY (job_id, attempt_number, sequence)
);
"""


class LocalHostAgent:
    def __init__(
        self,
        host_id: str,
        ledger_path: Path,
        *,
        backend: SandboxBackend,
        blobs: BlobStore,
        tools: ToolRegistry,
        work_root: Path,
        max_limits: ResourceLimits,
        allowed_effects: tuple[str, ...] = ("read_only", "scratch_only", "workspace_patch"),
        external: ExternalEffectAdapter | None = None,
        clock: Clock | None = None,
        crash_points: frozenset[str] = frozenset(),
        run_inline: bool = False,
    ) -> None:
        self.host_id = host_id
        self.backend = backend
        self.blobs = blobs
        self.tools = tools
        self.work_root = Path(work_root)
        self.max_limits = max_limits
        self.allowed_effects = allowed_effects
        self.external = external
        self.clock = clock or SystemClock()
        self.crash_points = crash_points
        self.run_inline = run_inline
        self.alive = True
        self._lock = threading.RLock()
        self._threads: list[threading.Thread] = []
        ledger_path = Path(ledger_path)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(ledger_path), isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.executescript(_LEDGER_SCHEMA)
        self._recover_after_restart()

    # ---------------------------------------------------------------- utils

    def _check_alive(self) -> None:
        if not self.alive:
            raise HostUnreachable(f"host {self.host_id} is not responding")

    def _crash_if(self, point: str) -> None:
        if point in self.crash_points:
            self.alive = False
            raise SimulatedCrash(point)

    def _append_event(self, spec_identity: tuple[str, int, int], kind: str, state: str, payload: dict) -> None:
        job_id, attempt, epoch = spec_identity
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                "SELECT last_seq FROM attempts WHERE job_id = ? AND attempt_number = ?", (job_id, attempt)
            ).fetchone()
            sequence = row["last_seq"] + 1
            self._db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, attempt, epoch, sequence, kind, state,
                 canonical_json_bytes(payload).decode()),
            )
            self._db.execute(
                "UPDATE attempts SET state = ?, last_seq = ? WHERE job_id = ? AND attempt_number = ?",
                (state, sequence, job_id, attempt),
            )
            self._db.execute("COMMIT")

    # ------------------------------------------------------------- protocol

    def get_capabilities(self) -> dict[str, Any]:
        self._check_alive()
        caps = self.backend.capabilities()
        return {"host_id": self.host_id, "backend": caps.model_dump(mode="json"),
                "max_limits": self.max_limits.model_dump(), "effects": list(self.allowed_effects)}

    def submit_job(self, spec_data: dict[str, Any], presented_digest: str) -> JobReceipt:
        self._check_alive()
        if len(canonical_json_bytes(spec_data)) > MAX_SPEC_BYTES:
            return self._reject(spec_data, presented_digest, "job spec exceeds the size limit")
        try:
            spec = JobSpec.model_validate(spec_data)
        except ValueError as error:
            return self._reject(spec_data, presented_digest, f"invalid job spec: {str(error)[:200]}")
        if spec.digest() != presented_digest:
            return self._reject(spec_data, presented_digest, "job digest does not match the spec")
        if spec.host_id != self.host_id:
            return self._reject(spec_data, presented_digest, f"job is addressed to {spec.host_id}, not {self.host_id}")
        if parse_timestamp(spec.expires_at) <= self.clock.now():
            return self._reject(spec_data, presented_digest, "job request has expired")
        over = self.max_limits.exceeded_by(spec.resource_budget)
        if over:
            return self._reject(spec_data, presented_digest, f"exceeds host limits: {', '.join(over)}")
        if spec.effect_class not in self.allowed_effects:
            return self._reject(spec_data, presented_digest, f"host does not run {spec.effect_class} jobs")

        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self._db.execute(
                "SELECT * FROM attempts WHERE job_id = ? AND attempt_number = ?",
                (spec.job_id, spec.attempt_number),
            ).fetchone()
            if existing is not None:
                self._db.execute("COMMIT")
                if existing["lease_epoch"] > spec.lease_epoch:
                    return self._reject(spec_data, presented_digest, "fenced: a newer lease epoch exists")
                if existing["spec_digest"] != presented_digest:
                    return self._reject(spec_data, presented_digest, "same attempt submitted with different content")
                return JobReceipt(
                    job_id=spec.job_id, attempt_number=spec.attempt_number, lease_epoch=spec.lease_epoch,
                    job_digest=presented_digest, host_id=self.host_id, accepted=True,
                    state=existing["state"], duplicate=True,
                )
            # Fence older attempts of the same job: they may not start or report.
            self._db.execute(
                "UPDATE attempts SET cancel_requested = 1 WHERE job_id = ? AND attempt_number < ?",
                (spec.job_id, spec.attempt_number),
            )
            self._db.execute(
                "INSERT INTO attempts (job_id, attempt_number, lease_epoch, spec_digest, spec_json, state)"
                " VALUES (?, ?, ?, ?, ?, 'queued')",
                (spec.job_id, spec.attempt_number, spec.lease_epoch, presented_digest,
                 canonical_json_bytes(spec.model_dump(mode="json")).decode()),
            )
            self._db.execute("COMMIT")
        self._append_event(spec.identity, "accepted", "leased", {})
        try:
            self._crash_if("after_receipt")
        except SimulatedCrash:
            # The request is durably recorded, but the reply never arrives.
            raise HostUnreachable(f"host {self.host_id} stopped before replying") from None
        if self.run_inline:
            self._execute_safely(spec)
        else:
            thread = threading.Thread(target=self._execute_safely, args=(spec,), daemon=True)
            self._threads.append(thread)
            thread.start()
        return JobReceipt(
            job_id=spec.job_id, attempt_number=spec.attempt_number, lease_epoch=spec.lease_epoch,
            job_digest=presented_digest, host_id=self.host_id, accepted=True, state="leased",
        )

    def _reject(self, spec_data: dict[str, Any], digest: str, reason: str) -> JobReceipt:
        return JobReceipt(
            job_id=str(spec_data.get("job_id", "unknown")), attempt_number=int(spec_data.get("attempt_number", 0) or 0),
            lease_epoch=int(spec_data.get("lease_epoch", 0) or 0), job_digest=digest
            if digest.startswith("sha256:") and len(digest) == 71 else "sha256:" + "0" * 64,
            host_id=self.host_id, accepted=False, state="failed", reason=reason,
        )

    def read_job_events(self, job_id: str, attempt_number: int, after_sequence: int, limit: int = 100) -> list[HostJobEvent]:
        self._check_alive()
        rows = self._db.execute(
            "SELECT * FROM events WHERE job_id = ? AND attempt_number = ? AND sequence > ?"
            " ORDER BY sequence LIMIT ?",
            (job_id, attempt_number, after_sequence, limit),
        ).fetchall()
        return [
            HostJobEvent(job_id=row["job_id"], attempt_number=row["attempt_number"], lease_epoch=row["lease_epoch"],
                         sequence=row["sequence"], kind=row["kind"], state=row["state"],
                         payload=json.loads(row["payload_json"]))
            for row in rows
        ]

    def get_job(self, job_id: str, attempt_number: int) -> str | None:
        self._check_alive()
        row = self._db.execute(
            "SELECT state FROM attempts WHERE job_id = ? AND attempt_number = ?", (job_id, attempt_number)
        ).fetchone()
        return None if row is None else row["state"]

    def cancel_job(self, job_id: str, attempt_number: int, lease_epoch: int, reason: str) -> bool:
        self._check_alive()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM attempts WHERE job_id = ? AND attempt_number = ?", (job_id, attempt_number)
            ).fetchone()
            if row is None or row["lease_epoch"] != lease_epoch:
                return False
            self._db.execute(
                "UPDATE attempts SET cancel_requested = 1 WHERE job_id = ? AND attempt_number = ?",
                (job_id, attempt_number),
            )
            state = row["state"]
        if state in TERMINAL_JOB_STATUSES:
            return True
        if state in ("queued", "leased"):
            self._append_event((job_id, attempt_number, lease_epoch), "cancel_acknowledged", "cancelled",
                               {"reason": reason, "stopped_before_start": True})
            return True
        self.backend.cancel(_job_key(job_id, attempt_number))
        return True

    def wait_idle(self, timeout: float = 30.0) -> None:
        for thread in list(self._threads):
            thread.join(timeout)

    def close(self) -> None:
        self.wait_idle()
        self._db.close()

    # ------------------------------------------------------------ execution

    def _cancel_requested(self, spec: JobSpec) -> bool:
        row = self._db.execute(
            "SELECT cancel_requested FROM attempts WHERE job_id = ? AND attempt_number = ?",
            (spec.job_id, spec.attempt_number),
        ).fetchone()
        return bool(row and row["cancel_requested"])

    def _execute_safely(self, spec: JobSpec) -> None:
        try:
            self._execute(spec)
        except SimulatedCrash:
            return  # the "process" died here; nothing more is written
        except Exception as error:  # noqa: BLE001 - reported as a failed execution
            self._append_event(spec.identity, "finished", "failed", {"error": f"{type(error).__name__}: {error}"[:500]})

    def _execute(self, spec: JobSpec) -> None:
        if self._cancel_requested(spec):
            state = self._db.execute(
                "SELECT state FROM attempts WHERE job_id = ? AND attempt_number = ?",
                (spec.job_id, spec.attempt_number),
            ).fetchone()["state"]
            if state not in TERMINAL_JOB_STATUSES:
                self._append_event(spec.identity, "cancel_acknowledged", "cancelled", {"stopped_before_start": True})
            return
        self._append_event(spec.identity, "started", "running", {})
        self._crash_if("after_start")
        kind = spec.arguments.get("kind")
        if kind == "sandbox_command":
            self._run_command(spec)
        elif kind == "external_effect":
            self._run_external(spec)
        else:
            self._append_event(spec.identity, "finished", "failed", {"error": f"unknown job kind {kind!r}"})

    def _run_command(self, spec: JobSpec) -> None:
        if self._cancel_requested(spec):
            self._append_event(spec.identity, "cancel_acknowledged", "cancelled", {"stopped_before_start": True})
            return
        tool = self.tools.resolve(str(spec.arguments["tool_id"]))
        if spec.tool_digest is not None and tool.executable_digest != spec.tool_digest:
            self._append_event(spec.identity, "finished", "failed", {"error": "tool digest differs from the job spec"})
            return
        job_dir = self.work_root / f"{spec.job_id}-{spec.attempt_number}"
        input_dir = job_dir / "input"
        manifest = load_manifest(self.blobs, spec.snapshot_digest)
        materialize(manifest, self.blobs, input_dir)
        args = [str(item) for item in spec.arguments.get("args", [])]
        sandbox = SandboxSpec(
            job_key=_job_key(spec.job_id, spec.attempt_number),
            input_dir=str(input_dir),
            argv=[tool.executable, *args],
            cwd="/input",
            limits=spec.resource_budget,
            toolchain_paths=tool.toolchain_paths,
        )
        run_dir = job_dir / "run"
        try:
            observation = self.backend.run(sandbox, run_dir)
            collected = collect_outputs(
                Path(observation.output_dir), self.blobs,
                max_files=spec.output_contract.max_files, max_bytes=spec.output_contract.max_bytes,
            )
            stdout = self.blobs.put_bytes(observation.stdout or b"")
            stderr = self.blobs.put_bytes(observation.stderr or b"")
        finally:
            destroy = getattr(self.backend, "destroy", None)
            if destroy is not None:
                destroy(run_dir)
            shutil.rmtree(job_dir, ignore_errors=True)
        cancelled = observation.terminated_by == "cancelled"
        state = "cancelled" if cancelled else ("failed" if observation.terminated_by == "launch_failed" else "succeeded")
        self._append_event(
            spec.identity,
            "cancel_acknowledged" if cancelled else "finished",
            state,
            {
                "exit_code": observation.exit_code,
                "signal": observation.signal,
                "terminated_by": observation.terminated_by,
                "wall_ms": observation.wall_ms,
                "processes_remaining": observation.processes_remaining,
                "stdout_digest": stdout.digest,
                "stderr_digest": stderr.digest,
                "stdout_truncated": observation.stdout_truncated,
                "outputs_accepted": collected.accepted,
                "outputs": [item.model_dump() for item in collected.files],
                "outputs_rejected": [item.model_dump() for item in collected.rejected],
            },
        )

    def _run_external(self, spec: JobSpec) -> None:
        if self.external is None:
            self._append_event(spec.identity, "finished", "failed", {"error": "no external adapter on this host"})
            return
        operation_id = str(spec.arguments["operation_id"])
        intent = {"operation_id": operation_id, "target": spec.arguments.get("target"),
                  "recorded_at": format_timestamp(self.clock.now())}
        with self._lock:
            self._db.execute(
                "UPDATE attempts SET intent_json = ? WHERE job_id = ? AND attempt_number = ?",
                (json.dumps(intent), spec.job_id, spec.attempt_number),
            )
        self._crash_if("after_intent")
        result = self.external.apply(operation_id, dict(spec.arguments.get("payload", {})))
        self._crash_if("after_effect")
        self._append_event(spec.identity, "finished", "succeeded", {"operation_id": operation_id, "result": result})

    # ------------------------------------------------------------- recovery

    def _recover_after_restart(self) -> None:
        """Attempts that were in flight when the host stopped are not resumed blindly."""
        rows = self._db.execute(
            "SELECT * FROM attempts WHERE state IN ('queued', 'leased', 'running')"
        ).fetchall()
        for row in rows:
            identity = (row["job_id"], row["attempt_number"], row["lease_epoch"])
            spec = json.loads(row["spec_json"])
            if row["intent_json"] is not None:
                operation_id = json.loads(row["intent_json"])["operation_id"]
                observed = self.external.lookup(operation_id) if self.external else "unknown"
                if observed == "applied":
                    self._append_event(identity, "finished", "succeeded",
                                       {"operation_id": operation_id, "reconciled": True})
                else:
                    self._append_event(identity, "lost", "lost",
                                       {"operation_id": operation_id, "target_state": observed,
                                        "reconciliation_required": True})
            elif spec["effect_class"] == "external_effect":
                # No intent recorded: the effect was never attempted.
                self._append_event(identity, "lost", "lost", {"target_state": "not_attempted"})
            else:
                self._append_event(identity, "lost", "lost", {"reason": "host restarted during execution"})


def _job_key(job_id: str, attempt: int) -> str:
    return f"job_{job_id.removeprefix('job_').replace('-', '')[:40]}_{attempt}"
