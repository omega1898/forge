"""Performance measurement and comparison (ticket F18).

A performance claim is admitted only when:

1. the candidate is correctness-qualified: every listed correctness
   criterion has a passing latest assessment for that exact snapshot (a
   faster candidate never compensates for an unmet correctness criterion);
2. the measurement held an exclusive machine lease, so no inference,
   indexing or evaluation ran alongside it (EX-T13);
3. no background interference was observed: during each repetition the
   whole machine's busy CPU time, minus the measured program's and the
   harness's own, stays under a noise floor, and hypervisor steal time is
   small; otherwise the measurement is invalid and the result inconclusive;
4. the candidate produced the same output as the baseline on the workload
   in every repetition (a program that detects the benchmark and skips work
   is caught);
5. the difference is outside a bootstrap confidence interval on the ratio
   of medians, computed with a fixed seed so the decision is reproducible.

Raw repetitions are preserved in a report artifact. Conditions (host, CPU,
kernel, virtualization, load, leases queued during the run) are recorded
with the evidence. Baseline and candidate are measured in alternating rounds
to reduce the effect of slow drift.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import random
import shutil
import statistics
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from forge.contracts.base import Slug, StrictModel
from forge.contracts.digests import canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.performance.scheduler import ResourceScheduler
from forge.platforms import where
from forge.sandbox.base import SandboxSpec

PERF_TOOL_REF = "forge.performance_comparison@1"
BOOTSTRAP_SEED = 42
BOOTSTRAP_ROUNDS = 2000

_HARNESS = r"""
import hashlib, json, os, resource, subprocess, sys, time
spec = json.loads(sys.argv[1])
tick_ns = 1_000_000_000 // os.sysconf("SC_CLK_TCK")
def machine():
    with open("/proc/stat") as handle:
        values = [int(v) for v in handle.readline().split()[1:]]
    values += [0] * (8 - len(values))
    return values[0] + values[1] + values[2] + values[5] + values[6], values[7]
def cpu(who):
    usage = resource.getrusage(who)
    return int((usage.ru_utime + usage.ru_stime) * 1e9)
stdin = bytes.fromhex(spec["stdin_hex"])
samples = []
for index in range(spec["warmup"] + spec["repetitions"]):
    busy0, steal0 = machine(); child0 = cpu(resource.RUSAGE_CHILDREN); self0 = cpu(resource.RUSAGE_SELF)
    start = time.perf_counter_ns()
    done = subprocess.run(spec["argv"], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    end = time.perf_counter_ns()
    busy1, steal1 = machine(); child1 = cpu(resource.RUSAGE_CHILDREN); self1 = cpu(resource.RUSAGE_SELF)
    if index < spec["warmup"]:
        continue
    samples.append({"wall_ns": end - start, "child_cpu_ns": child1 - child0, "harness_cpu_ns": self1 - self0,
                    "machine_busy_ns": (busy1 - busy0) * tick_ns, "steal_ns": (steal1 - steal0) * tick_ns,
                    "exit_code": done.returncode,
                    "stdout_digest": "sha256:" + hashlib.sha256(done.stdout).hexdigest()})
print(json.dumps({"samples": samples, "tick_ns": tick_ns}))
"""


class Workload(StrictModel):
    """What is measured. Evaluation workloads are reserved: optimization never reads them."""

    workload_id: Slug
    purpose: Literal["optimization", "evaluation"]
    description: str = Field(max_length=1000)
    args: list[str] = Field(default_factory=list, max_length=64)
    files: dict[str, str] = Field(default_factory=dict)      # base64, mounted read-only under /case
    stdin_b64: str = ""
    repetitions: int = Field(default=10, ge=4, le=200)
    warmup: int = Field(default=1, ge=0, le=20)

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


class Sample(StrictModel):
    wall_ns: int
    child_cpu_ns: int
    harness_cpu_ns: int
    machine_busy_ns: int
    steal_ns: int
    exit_code: int
    stdout_digest: str

    def other_cpu_ns(self) -> int:
        """Busy CPU time on the machine that neither the program nor the harness used."""
        return self.machine_busy_ns - self.child_cpu_ns - self.harness_cpu_ns


class Measurement(StrictModel):
    snapshot_id: str
    samples: list[Sample]
    problems: list[str]


def interference(samples: list[Sample], *, tick_ns: int, cpus: int) -> list[str]:
    """Reasons the samples cannot be trusted as quiet-machine measurements."""
    problems = []
    # Counters move in whole ticks per CPU; allow that quantization plus 30% of wall time.
    for index, sample in enumerate(samples):
        floor = int(0.3 * sample.wall_ns) + cpus * tick_ns + 20_000_000
        if sample.other_cpu_ns() > floor:
            problems.append(f"repetition {index}: {sample.other_cpu_ns() // 1_000_000} ms of other CPU activity "
                            f"during {sample.wall_ns // 1_000_000} ms")
        if sample.steal_ns > max(int(0.1 * sample.wall_ns), 2 * tick_ns):
            problems.append(f"repetition {index}: the hypervisor took {sample.steal_ns // 1_000_000} ms (steal)")
    return problems


def ratio_interval(baseline: list[int], candidate: list[int], *, seed: int = BOOTSTRAP_SEED,
                   rounds: int = BOOTSTRAP_ROUNDS) -> tuple[float, float, float]:
    """Ratio of medians (candidate / baseline) and its 95% percentile-bootstrap interval."""
    if not baseline or not candidate:
        raise ValueError("both groups need samples")
    point = statistics.median(candidate) / statistics.median(baseline)
    generator = random.Random(seed)
    ratios = []
    for _ in range(rounds):
        b = [generator.choice(baseline) for _ in baseline]
        c = [generator.choice(candidate) for _ in candidate]
        ratios.append(statistics.median(c) / statistics.median(b))
    ratios.sort()
    return point, ratios[int(0.025 * rounds)], ratios[int(0.975 * rounds) - 1]


def coefficient_of_variation(values: list[int]) -> float:
    return statistics.pstdev(values) / statistics.mean(values) if len(values) > 1 and statistics.mean(values) else 0.0


def decide(goal: str, low: float, high: float, *, min_improvement: float, max_regression: float) -> tuple[str, str]:
    """Map an interval on candidate/baseline time to an assessment result."""
    if goal == "improve":
        target = 1 - min_improvement
        if high < target:
            return "pass", f"faster by at least {min_improvement:.0%} (interval {low:.3f}-{high:.3f})"
        if low > target:
            return "fail", f"the required {min_improvement:.0%} improvement is not reached (interval {low:.3f}-{high:.3f})"
        return "inconclusive", f"the interval {low:.3f}-{high:.3f} straddles the target ratio {target:.3f}"
    limit = 1 + max_regression
    if high < limit:
        return "pass", f"no regression beyond {max_regression:.0%} (interval {low:.3f}-{high:.3f})"
    if low > limit:
        return "fail", f"slower by more than {max_regression:.0%} (interval {low:.3f}-{high:.3f})"
    return "inconclusive", f"the interval {low:.3f}-{high:.3f} straddles the limit {limit:.3f}"


def host_description() -> dict[str, Any]:
    cpu_model = ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            cpu_model = next((line.split(":", 1)[1].strip() for line in handle if line.startswith("model name")), "")
    except OSError:
        pass
    governor = None
    try:
        governor = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
    except OSError:
        pass
    return {"host_name": platform.node(), "cpu_model": cpu_model, "logical_cpus": os.cpu_count() or 1,
            "kernel": platform.release(), "governor": governor}


class PerformanceService:
    def __init__(self, assessment, scheduler: ResourceScheduler, *, rounds: int = 2,  # noqa: ANN001
                 lease_timeout_s: float = 600.0) -> None:
        self.assessment = assessment
        self.scheduler = scheduler
        self.rounds = rounds
        self.lease_timeout_s = lease_timeout_s

    # ------------------------------------------------------------ helpers

    def _qualification(self, criterion_ids: list[str], snapshot_id: str) -> list[str]:
        store = self.assessment.controller.store
        problems = []
        for criterion_id in criterion_ids:
            criterion = store.criterion(criterion_id)
            found = [a for a in store.assessments(criterion_id=criterion_id, snapshot_id=snapshot_id)
                     if criterion is not None and a.criterion_ref.revision == criterion.revision]
            if not found:
                problems.append(f"{criterion_id} has no assessment for this snapshot")
                continue
            latest = max(found, key=lambda a: (a.recorded_seq or 0, a.created_at))
            if latest.assessment_result != "pass":
                problems.append(f"{criterion_id} is assessed {latest.assessment_result}")
        return problems

    def _run(self, snapshot_dir: Path, workload: Workload, entrypoint: list[str], repetitions: int) -> dict[str, Any]:
        service = self.assessment
        case_dir = service.work_root / f"{new_id('job')}-case"
        case_dir.mkdir(parents=True)
        os.chmod(case_dir, 0o755)
        for name, encoded in workload.files.items():
            target = case_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded))
            os.chmod(target, 0o644)
        argv = [service.python_path if part == "{python}" else part for part in entrypoint]
        argv += [arg.replace("{case}", "/case") for arg in workload.args]
        spec = {"argv": argv, "repetitions": repetitions, "warmup": workload.warmup,
                "stdin_hex": base64.b64decode(workload.stdin_b64).hex()}
        work = service.work_root / new_id("job")
        limits = service.limits.model_copy(update={"wall_ms": max(service.limits.wall_ms, 300_000),
                                                   "cpu_seconds": max(service.limits.cpu_seconds, 240)})
        try:
            observation = service.backend.run(SandboxSpec(
                job_key="perf_measure", input_dir=str(snapshot_dir), case_dir=str(case_dir), cwd="/input",
                argv=[service.python_path, "-I", "-c", _HARNESS, json.dumps(spec)], limits=limits,
                toolchain_paths=service.python_paths), work)
        finally:
            destroy = getattr(service.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(case_dir, ignore_errors=True)
        if observation.terminated_by != "exited" or observation.exit_code != 0:
            return {"samples": [], "tick_ns": 10_000_000,
                    "error": f"measurement harness {observation.terminated_by} (exit {observation.exit_code}): "
                             + observation.stderr[-300:].decode("utf-8", "replace").strip()}
        try:
            return json.loads(observation.stdout)
        except ValueError:
            return {"samples": [], "tick_ns": 10_000_000, "error": "measurement harness output is not JSON"}

    # ------------------------------------------------------------- assess

    def assess(self, criterion_id: str, candidate_snapshot_id: str) -> dict[str, Any]:
        service = self.assessment
        store = service.controller.store
        criterion = store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "performance_comparison":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not a performance_comparison criterion")
        parameters = criterion.parameters
        workload = Workload.model_validate(parameters["workload"])
        if workload.purpose != "evaluation":
            raise ForgeError(ErrorCode.INVALID_REQUEST, "a performance criterion measures on a reserved evaluation "
                                                        "workload, not the one used for optimization")
        goal = parameters.get("goal", "improve")
        baseline_snapshot_id = str(parameters["baseline_snapshot_id"])
        criterion_ref = {"id": criterion.criterion_id, "revision": criterion.revision}
        caps = service.backend.capabilities()

        def record(result: str, limitations: list[str], evidence: list[str], summary: dict[str, Any]) -> str:
            return service._send("assessment.record", {
                "criterion_ref": criterion_ref, "snapshot_id": candidate_snapshot_id, "evidence_refs": evidence,
                "assessment_result": result, "assessor_ref": PERF_TOOL_REF, "limitations": limitations,
                "summary": {**summary, "environment": f"{caps.os}_{caps.arch}", "executed_on": where(caps)}}
            ).result["assessment_id"]

        unqualified = self._qualification(list(parameters.get("correctness_criteria", [])), candidate_snapshot_id)
        if unqualified:
            reason = "the candidate is not correctness-qualified: " + "; ".join(unqualified)
            return {"assessment_id": record("not_run", [reason], [], {"qualified": False}), "result": "not_run",
                    "reason": reason}
        if not caps.available:
            reason = f"isolation backend unavailable: {caps.unavailable_reason}"
            return {"assessment_id": record("not_run", [reason], [], {}), "result": "not_run", "reason": reason}

        entrypoint = list(parameters["entrypoint"])
        per_round = max(2, workload.repetitions // self.rounds)
        directories = {name: service._materialize(snapshot)
                       for name, snapshot in (("baseline", baseline_snapshot_id), ("candidate", candidate_snapshot_id))}
        raw: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
        errors: list[str] = []
        tick_ns = 10_000_000
        load_before = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
        lease = self.scheduler.acquire("benchmark", f"perf:{criterion_id}", timeout_s=self.lease_timeout_s)
        if lease is None:
            for path in directories.values():
                shutil.rmtree(path, ignore_errors=True)
            reason = "the machine could not be reserved for an exclusive measurement"
            return {"assessment_id": record("not_run", [reason], [], {}), "result": "not_run", "reason": reason}
        try:
            for _ in range(self.rounds):
                for name in ("baseline", "candidate"):
                    result = self._run(directories[name], workload, entrypoint, per_round)
                    raw[name].extend(result["samples"])
                    tick_ns = result.get("tick_ns", tick_ns)
                    if "error" in result:
                        errors.append(f"{name}: {result['error']}")
        finally:
            self.scheduler.release(lease)
            for path in directories.values():
                shutil.rmtree(path, ignore_errors=True)
        load_after = os.getloadavg()[0] if hasattr(os, "getloadavg") else None

        samples = {name: [Sample.model_validate(item) for item in items] for name, items in raw.items()}
        overlapping = [f"{other.kind}:{other.holder}" for other in self.scheduler.overlapping(lease)]
        queued = [f"{other.kind}:{other.holder}" for other in self.scheduler.queued_during(lease)]
        host = host_description()
        problems = list(errors)
        if overlapping:
            problems.append(f"other work held the machine during the measurement: {overlapping}")
        for name, group in samples.items():
            problems += [f"{name} {item}" for item in interference(group, tick_ns=tick_ns, cpus=host["logical_cpus"])]
            if any(s.exit_code != 0 for s in group):
                problems.append(f"{name}: the program failed during measurement")
        digests = {s.stdout_digest for group in samples.values() for s in group}
        if len(digests) > 1:
            problems.append("the program's output on the workload differs between runs or from the baseline")

        conditions = {**host, "virtualization": caps.virtualization, "backend": caps.backend_id,
                      "load_before": load_before, "load_after": load_after, "exclusive_lease": lease.lease_id,
                      "overlapping_leases": overlapping, "queued_during": queued,
                      "workload_digest": workload.digest(), "rounds": self.rounds, "repetitions_per_round": per_round}
        summary: dict[str, Any] = {"host": host["host_name"], "goal": goal, "qualified": True}
        limitations = ["measured on one named host under the recorded conditions; other machines may differ",
                       "wall-clock time of whole program runs, including start-up; not a latency percentile"]
        if not problems and all(samples.values()):
            base_times = [s.wall_ns for s in samples["baseline"]]
            cand_times = [s.wall_ns for s in samples["candidate"]]
            point, low, high = ratio_interval(base_times, cand_times)
            noisy = [name for name, values in (("baseline", base_times), ("candidate", cand_times))
                     if coefficient_of_variation(values) > float(parameters.get("max_cv", 0.25))]
            summary.update({"ratio": round(point, 4), "interval": [round(low, 4), round(high, 4)],
                            "baseline_median_ms": round(statistics.median(base_times) / 1e6, 3),
                            "candidate_median_ms": round(statistics.median(cand_times) / 1e6, 3)})
            if noisy:
                result, reason = "inconclusive", f"too much variation between repetitions: {', '.join(noisy)}"
            else:
                result, reason = decide(goal, low, high, min_improvement=float(parameters.get("min_improvement", 0.1)),
                                        max_regression=float(parameters.get("max_regression", 0.05)))
        else:
            result = "inconclusive"
            reason = "the measurement is invalid: " + "; ".join(problems[:5])
        summary["reason"] = reason[:1000]
        limitations.append(reason[:1000])

        report = service.controller.blobs.put_bytes(canonical_json_bytes(
            {"samples": {name: [s.model_dump() for s in group] for name, group in samples.items()},
             "conditions": conditions, "problems": problems, "summary": summary}))
        report_id = service._send("artifact.register", {
            "digest": report.digest, "size": report.size, "content_type": "application/json",
            "artifact_kind": "performance_report", "sensitivity": "project"}).result["artifact_id"]
        evidence_id = service._send("evidence.record_execution", {
            "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": candidate_snapshot_id,
            "tool_ref": PERF_TOOL_REF, "environment_digest": digest_json(conditions),
            "observation_type": "performance_measurement",
            "observation": {"conditions": conditions, "problems": problems[:20], "summary": summary,
                            "baseline_snapshot_id": baseline_snapshot_id},
            "artifact_refs": [report_id],
        }).result["evidence_id"]
        assessment_id = record(result, limitations, [evidence_id], summary)
        return {"assessment_id": assessment_id, "result": result, "reason": reason, "evidence_id": evidence_id,
                "conditions": conditions, "problems": problems, "summary": summary, "samples": samples}
