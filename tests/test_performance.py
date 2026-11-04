"""Ticket F18 (performance workflow), EX-T13 (exclusive measurement) and D09 (bridge, reconciliation).

Acceptance: a correctness-qualified candidate is measured on a named host;
background interference invalidates the measurement; a benchmark runs while
background inference is queued, the scheduler honors exclusivity and the
measured conditions are recorded.
"""

from __future__ import annotations

import base64
import random
import subprocess
import sys
import threading
import time
from datetime import date
from fractions import Fraction

import pytest

from forge.assessment.service import AssessmentService
from forge.assessment.suite import EvaluatorSuite
from forge.contracts.errors import ForgeError
from forge.gateway.adapters import FakeModelAdapter
from forge.gateway.contracts import Message
from forge.gateway.proposals import AgentProposal
from forge.gateway.schema_subset import flat_schema
from forge.gateway.service import ModelGateway
from forge.performance.bridge import (
    aging_bucket,
    cost_bridge,
    exceeds_threshold,
    price_volume_bridge,
    reconcile,
)
from forge.performance.measure import (
    PerformanceService,
    Sample,
    decide,
    interference,
    ratio_interval,
)
from forge.performance.scheduler import ResourceScheduler
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from tests.conftest import command
from tests.test_gateway import PROBE_OK, VALID, config

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason)
)


# ----------------------------------------------------------------- D09 bridge


def test_the_skills_own_example_ties_out_only_with_the_interaction_term() -> None:
    bridge = price_volume_bridge(10_000, 50, 11_000, 48)
    assert bridge.change == 28_000
    assert bridge.drivers == {"volume": 50_000, "price": -20_000, "interaction": -2_000}
    with pytest.raises(ForgeError, match="does not tie out"):
        price_volume_bridge(10_000, 50, 11_000, 48, include_interaction=False)


def test_cost_bridges_tie_out_exactly_for_any_inputs() -> None:
    generator = random.Random(9)
    for _ in range(300):
        keys = [f"endpoint_{n}" for n in range(generator.randint(1, 6))]
        base = {k: (generator.randint(0, 500), round(generator.uniform(0, 50), 2)) for k in keys
                if generator.random() < 0.8}
        actual = {k: (generator.randint(0, 500), round(generator.uniform(0, 50), 2)) for k in keys
                  if generator.random() < 0.8}
        if not base:
            continue
        bridge = cost_bridge(base, actual)
        assert bridge.ties_out()
        assert bridge.start + sum(bridge.drivers.values(), Fraction(0)) == bridge.end


def test_float_inputs_are_exact_and_percentiles_are_refused() -> None:
    bridge = cost_bridge({"a": (3, 0.1)}, {"a": (3, 0.2)})
    assert bridge.drivers["rate"] == Fraction(3, 10)        # not 0.30000000000000004
    with pytest.raises(ForgeError, match="not additive"):
        cost_bridge({"a": (1, 1)}, {"a": (1, 2)}, metric="p99")


def test_thresholds_trigger_on_either_limit() -> None:
    assert exceeds_threshold(600, 100_000, absolute=500, percent=5)          # absolute only
    assert exceeds_threshold(60, 1_000, absolute=500, percent=5)             # percent only
    assert not exceeds_threshold(40, 1_000, absolute=500, percent=5)
    assert not exceeds_threshold(0, 0, absolute=500, percent=5)


def test_aging_buckets_do_not_overlap() -> None:
    assert [aging_bucket(d) for d in (0, 30, 31, 60, 61, 90, 91, 400)] == [
        "0-30 days", "0-30 days", "31-60 days", "31-60 days", "61-90 days", "61-90 days", "over 90 days",
        "over 90 days"]
    with pytest.raises(ValueError):
        aging_bucket(-1)


def test_reconciliation_keeps_every_seeded_defect_visible() -> None:
    as_of = date(2026, 11, 4)
    left = {"r1": {"amount": "10.00", "date": "2026-11-01"},
            "r2": {"amount": "20.00", "date": "2026-10-01"},     # dropped on the right, old: exception
            "r3": {"amount": "30.00", "date": "2026-11-03"},     # dropped on the right, young: timing
            "r4": {"amount": "40.00", "date": "2026-07-01"}}     # one-cent rounding change: exception
    right = {"r1": {"amount": "10.00", "date": "2026-11-01"},
             "r4": {"amount": "40.01", "date": "2026-07-01"},
             "r5": {"amount": "5.00", "date": "2026-11-04"}}     # not yet on the left: timing
    result = reconcile(left, right, fields=["amount"], as_of=as_of, timing_window_days=5,
                       prepared_by="actor_migration", reconciled_by="actor_reviewer")
    assert result.matched == ["r1"]
    assert sorted(result.timing) == ["left:r3", "right:r5"]
    kinds = {(e["key"], e["kind"], e["aging"]) for e in result.exceptions}
    assert kinds == {("r2", "left_only", "31-60 days"), ("r4", "mismatch", "over 90 days")}
    assert result.ties_out()
    with pytest.raises(ForgeError, match="must not be the actor"):
        reconcile(left, right, fields=["amount"], as_of=as_of, timing_window_days=5,
                  prepared_by="actor_x", reconciled_by="actor_x")


# --------------------------------------------------------- statistics units


def sample(wall_ms: float, other_ms: float = 0, steal_ms: float = 0, digest: str = "sha256:a") -> Sample:
    wall = int(wall_ms * 1e6)
    return Sample(wall_ns=wall, child_cpu_ns=wall, harness_cpu_ns=1_000_000,
                  machine_busy_ns=wall + 1_000_000 + int(other_ms * 1e6), steal_ns=int(steal_ms * 1e6),
                  exit_code=0, stdout_digest=digest)


def test_interference_is_detected_from_other_cpu_time_and_steal() -> None:
    quiet = [sample(200), sample(210, other_ms=30)]
    assert interference(quiet, tick_ns=10_000_000, cpus=4) == []
    noisy = interference([sample(200, other_ms=190)], tick_ns=10_000_000, cpus=4)
    assert noisy and "other CPU activity" in noisy[0]
    assert "steal" in interference([sample(200, steal_ms=60)], tick_ns=10_000_000, cpus=4)[0]


def test_the_ratio_interval_is_reproducible_and_decisions_follow_it() -> None:
    baseline, candidate = [100, 102, 98, 101, 99, 100], [50, 51, 49, 50, 52, 48]
    first = ratio_interval(baseline, candidate)
    assert first == ratio_interval(baseline, candidate)
    point, low, high = first
    assert 0.45 < low <= point <= high < 0.55
    assert decide("improve", low, high, min_improvement=0.1, max_regression=0.05)[0] == "pass"
    assert decide("improve", 0.95, 0.99, min_improvement=0.1, max_regression=0.05)[0] == "fail"
    assert decide("improve", 0.85, 0.95, min_improvement=0.1, max_regression=0.05)[0] == "inconclusive"
    assert decide("no_regression", 0.98, 1.03, min_improvement=0.1, max_regression=0.05)[0] == "pass"
    assert decide("no_regression", 1.10, 1.20, min_improvement=0.1, max_regression=0.05)[0] == "fail"


# ------------------------------------------------------------- scheduler


def test_an_exclusive_lease_waits_for_shared_work_and_blocks_new_work() -> None:
    scheduler = ResourceScheduler()
    inference = scheduler.acquire("inference", "call_1")
    granted = {}

    def benchmark() -> None:
        granted["benchmark"] = scheduler.acquire("benchmark", "perf")

    thread = threading.Thread(target=benchmark, daemon=True)
    thread.start()
    time.sleep(0.1)
    assert "benchmark" not in granted                       # waits for the running inference
    # New shared work queues behind the waiting benchmark instead of starving it.
    assert scheduler.acquire("indexing", "index_1", timeout_s=0.1) is None
    scheduler.release(inference)
    thread.join(2)
    lease = granted["benchmark"]
    assert lease is not None and scheduler.active() == [lease]
    assert scheduler.acquire("inference", "call_2", timeout_s=0.05) is None
    scheduler.release(lease)
    later = scheduler.acquire("inference", "call_3", timeout_s=1)
    assert later is not None and later.granted_ns >= lease.released_ns
    with pytest.raises(ForgeError):
        scheduler.release(lease)


def test_queued_inference_waits_for_the_benchmark_through_the_gateway(world) -> None:  # noqa: ANN001
    """EX-T13: the gateway's inference call queues behind an exclusive measurement."""
    scheduler = ResourceScheduler()
    fake = FakeModelAdapter(list(PROBE_OK))
    gateway = ModelGateway(world.controller, adapters={"fake": fake}, scheduler=scheduler)
    gateway.register(config())
    gateway.probe("local_small")
    fake.script({"kind": "text", "text": __import__("json").dumps(VALID)})
    benchmark = scheduler.acquire("benchmark", "perf")
    results = {}

    def call() -> None:
        results["call"] = gateway.generate(
            model_config_id="local_small", slot_id="next_step", messages=[Message(role="user", content="next?")],
            output_model=AgentProposal, output_schema=flat_schema(AgentProposal), deadline_ms=5_000)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    time.sleep(0.2)
    assert "call" not in results                          # queued, not running
    scheduler.release(benchmark)
    thread.join(5)
    assert results["call"].status == "valid"
    [queued] = scheduler.queued_during(benchmark)
    assert queued.kind == "inference" and queued.granted_ns >= benchmark.released_ns
    assert scheduler.overlapping(benchmark) == []


def test_inference_gives_up_cleanly_if_the_benchmark_outlasts_its_deadline(world) -> None:  # noqa: ANN001
    scheduler = ResourceScheduler()
    fake = FakeModelAdapter(list(PROBE_OK))
    gateway = ModelGateway(world.controller, adapters={"fake": fake}, scheduler=scheduler)
    gateway.register(config())
    gateway.probe("local_small")
    benchmark = scheduler.acquire("benchmark", "perf")
    result = gateway.generate(model_config_id="local_small", slot_id="next_step",
                              messages=[Message(role="user", content="next?")], output_model=AgentProposal,
                              output_schema=flat_schema(AgentProposal), deadline_ms=200)
    assert result.status == "timeout" and "exclusive measurement" in " ".join(result.errors)
    scheduler.release(benchmark)
    assert scheduler.active() == []                      # nothing leaked


# ------------------------------------------------------ end to end (sandbox)

SLOW = "import sys\nn = int(open(sys.argv[1]).read())\ntotal = 0\nfor i in range(n):\n    total += i * i\nprint(total)\n"
FAST = "import sys\nn = int(open(sys.argv[1]).read())\nprint((n - 1) * n * (2 * n - 1) // 6)\n"
WRONG_FAST = "import sys\nn = int(open(sys.argv[1]).read())\nprint(n * n)\n"


def correctness_suite() -> dict:
    cases = [{"case_id": f"n{n}", "description": f"sum of squares below {n}", "args": ["{case}/n.txt"],
              "files": {"n.txt": base64.b64encode(str(n).encode()).decode()},
              "expect": {"exit_code": 0, "stdout_text": f"{sum(i * i for i in range(n))}\n"}}
             for n in (0, 1, 2, 10, 1000)]
    return EvaluatorSuite(suite_id="sum_squares", revision=1, description="sum of squares",
                          entrypoint=["{python}", "-I", "squares.py"], cases=cases).model_dump(mode="json")


@pytest.fixture
def perf(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Squares", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)

    def snapshot(program: str) -> str:
        (source / "squares.py").write_text(program)
        return controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]

    def send(method: str, params: dict) -> dict:
        return controller.apply_command(command(method, params, project_id=project_id), human).result

    suite = send("suite.admit", {"suite": correctness_suite()})
    correctness = send("criterion.create", {
        "subject": "Sum of squares is exact", "assessment_method": "black_box_cases",
        "parameters": {"suite_artifact_id": suite["suite_artifact_id"], "suite_digest": suite["suite_digest"]}}
    )["criterion_id"]
    baseline = snapshot(SLOW)
    workload = {"workload_id": "large_n", "purpose": "evaluation", "description": "n = 2,000,000",
                "args": ["{case}/n.txt"], "files": {"n.txt": base64.b64encode(b"2000000").decode()},
                "repetitions": 6, "warmup": 1}
    criterion = send("criterion.create", {
        "subject": "At least 10% faster on the large workload", "assessment_method": "performance_comparison",
        "parameters": {"workload": workload, "entrypoint": ["{python}", "-I", "squares.py"],
                       "baseline_snapshot_id": baseline, "goal": "improve", "min_improvement": 0.1,
                       "correctness_criteria": [correctness]}})["criterion_id"]
    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    scheduler = ResourceScheduler()
    return {"snapshot": snapshot, "correctness": correctness, "criterion": criterion, "service": service,
            "scheduler": scheduler, "performance": PerformanceService(service, scheduler),
            "store": controller.store, "send": send}


@needs_sandbox
def test_a_correct_faster_candidate_is_measured_on_a_named_host(perf) -> None:
    candidate = perf["snapshot"](FAST)
    assert perf["service"].assess_black_box(perf["correctness"], candidate)["result"] == "pass"
    outcome = perf["performance"].assess(perf["criterion"], candidate)
    if outcome["result"] == "inconclusive" and ("other CPU activity" in outcome["reason"]
                                                or "steal" in outcome["reason"]):
        pytest.skip(f"this machine is too noisy to measure (the system said so): {outcome['reason'][:200]}")
    assert outcome["result"] == "pass", outcome["reason"]
    conditions = outcome["conditions"]
    assert conditions["host_name"] and conditions["cpu_model"] and conditions["logical_cpus"] >= 1
    assert conditions["overlapping_leases"] == [] and conditions["exclusive_lease"]
    assert outcome["summary"]["ratio"] < 0.5
    evidence = perf["store"].evidence(outcome["evidence_id"])
    assert evidence.observation["conditions"]["workload_digest"].startswith("sha256:")
    assert len(outcome["samples"]["baseline"]) == len(outcome["samples"]["candidate"]) == 6


@needs_sandbox
def test_a_faster_candidate_without_correctness_evidence_is_not_measured(perf) -> None:
    candidate = perf["snapshot"](WRONG_FAST)
    outcome = perf["performance"].assess(perf["criterion"], candidate)
    assert outcome["result"] == "not_run" and "no assessment" in outcome["reason"]
    assert perf["service"].assess_black_box(perf["correctness"], candidate)["result"] == "fail"
    outcome = perf["performance"].assess(perf["criterion"], candidate)
    assert outcome["result"] == "not_run" and "assessed fail" in outcome["reason"]


@needs_sandbox
def test_background_cpu_load_invalidates_the_measurement(perf) -> None:
    candidate = perf["snapshot"](FAST)
    perf["service"].assess_black_box(perf["correctness"], candidate)
    burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(2)]
    try:
        time.sleep(0.2)
        outcome = perf["performance"].assess(perf["criterion"], candidate)
    finally:
        for burner in burners:
            burner.kill()
            burner.wait()
    assert outcome["result"] == "inconclusive"
    assert "other CPU activity" in outcome["reason"]
    assert "ratio" not in outcome["summary"]               # no number is reported from a disturbed run


@needs_sandbox
def test_a_candidate_whose_output_changes_under_measurement_is_rejected(perf) -> None:
    # Correct on the small correctness cases, wrong on the large workload: it
    # gives up above a size it guesses only a benchmark would use.
    sneaky = FAST.replace("print((n - 1)", "print(0 if n > 1_000_000 else (n - 1)")
    candidate = perf["snapshot"](sneaky)
    assert perf["service"].assess_black_box(perf["correctness"], candidate)["result"] == "pass"
    outcome = perf["performance"].assess(perf["criterion"], candidate)
    assert outcome["result"] == "inconclusive" and "output on the workload differs" in outcome["reason"]


class LeakyScheduler(ResourceScheduler):
    """A scheduler with a defect: it reports work that overlapped the benchmark."""

    def overlapping(self, lease):  # noqa: ANN001, ANN201
        return [type(lease)(lease_id="job_x", kind="inference", holder="call_9", requested_ns=0, granted_ns=0)]


@needs_sandbox
def test_overlapping_work_invalidates_the_measurement_even_if_the_scheduler_is_wrong(perf) -> None:
    candidate = perf["snapshot"](FAST)
    perf["service"].assess_black_box(perf["correctness"], candidate)
    outcome = PerformanceService(perf["service"], LeakyScheduler()).assess(perf["criterion"], candidate)
    assert outcome["result"] == "inconclusive" and "inference:call_9" in outcome["reason"]


def test_optimization_workloads_cannot_back_a_criterion(world) -> None:  # noqa: ANN001
    criterion = world.send("criterion.create", {
        "subject": "Faster", "assessment_method": "performance_comparison",
        "parameters": {"workload": {"workload_id": "tuning", "purpose": "optimization", "description": "d"},
                       "entrypoint": ["{python}", "x.py"], "baseline_snapshot_id": "snapshot_x"}}
    ).result["criterion_id"]

    class OnlyTheRecord:
        controller = world.controller

    with pytest.raises(ForgeError, match="reserved evaluation workload"):
        PerformanceService(OnlyTheRecord(), ResourceScheduler()).assess(criterion, "snapshot_y")
