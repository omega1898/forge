"""build_tool: objective -> plan -> acceptance -> implementation -> assessment -> package -> review.

The first complete FORGE workflow (ticket F11), the "walking skeleton". Every
step is a recorded command; the model only proposes file contents through a
validated slot, and FORGE's evaluators decide whether they are good:

1. record the request (intent) and its planning task;
2. admit the human-authored acceptance suite; create three criteria:
   behavior (black-box cases), static review (analyzer verdict), packaging;
3. apply a deterministic plan: design -> implement -> package -> review;
4. implement: context bundle -> `write_files` slot -> candidate snapshot ->
   behavior and static assessments; on failure, a bounded repair with the
   failing cases' *locations* (never the expected answers); identical
   candidates count as no progress;
5. package: build, install into a clean environment, run the command;
6. review: a release decision that only the human can record.

When the repair budget runs out the task waits for the user, with the
current candidate and its failures; the acceptance threshold is never
lowered.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.commands import ActorContext
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.contracts.versions import SCHEMA_VERSION
from forge.gateway.slots import SlotContext, SlotContract, cites_known_records, run_slot
from forge.workspace.candidates import create_candidate

BUDGET = {"max_wall_ms": 3_600_000, "max_attempts": 3}


class ProposedFile(StrictModel):
    path: str = Field(max_length=200)
    content: str = Field(max_length=200_000)


class FilesProposal(StrictModel):
    files: list[ProposedFile] = Field(min_length=1, max_length=12)
    rationale: str = Field(max_length=1500)
    record_ids: list[str] = Field(default_factory=list, max_length=16)


def paths_within(prefixes: tuple[str, ...]):  # noqa: ANN201
    def check(output: FilesProposal, context: SlotContext) -> list[str]:
        problems = []
        for item in output.files:
            try:
                check_relative_path(item.path)
            except ValueError as error:
                problems.append(str(error))
                continue
            if not any(item.path == p or item.path.startswith(p) for p in prefixes):
                problems.append(f"{item.path} is outside the allowed edit scope {list(prefixes)}")
        return problems

    return check


def python_parses(output: FilesProposal, context: SlotContext) -> list[str]:
    """Cheap check before expensive ones: proposed Python must at least parse."""
    problems = []
    for item in output.files:
        if item.path.endswith(".py"):
            try:
                ast.parse(item.content, filename=item.path)
            except SyntaxError as error:
                problems.append(f"{item.path} line {error.lineno}: syntax error: {error.msg}")
    return problems


@dataclass
class BuildToolSpec:
    name: str
    request_text: str
    suite: dict[str, Any]
    console_script: str
    allowed_paths: tuple[str, ...] = ("src/", "pyproject.toml", "README.md")
    max_repairs: int = 2
    context_budget_tokens: int = 6000


@dataclass
class BuildReport:
    status: str
    intent_id: str = ""
    task_ids: dict[str, str] = field(default_factory=dict)
    criterion_ids: dict[str, str] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    final_snapshot_id: str | None = None
    assessments: list[dict[str, Any]] = field(default_factory=list)
    package: dict[str, Any] | None = None
    decision_id: str | None = None
    blocker: str | None = None
    repair_prompts: list[str] = field(default_factory=list)


class BuildToolWorkflow:
    def __init__(self, controller, *, human: ActorContext, assessment, gateway, model_config_id: str,  # noqa: ANN001
                 context, ruff=None) -> None:  # noqa: ANN001
        self.controller, self.human = controller, human
        self.assessment, self.gateway, self.model_config_id = assessment, gateway, model_config_id
        self.context, self.ruff = context, ruff

    # -------------------------------------------------------------- helpers

    def _cmd(self, method: str, params: dict[str, Any], *, actor: ActorContext | None = None,
             expected: int | None = None) -> Any:
        envelope: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "method": method,
                                    "idempotency_key": new_id("request"), "params": params}
        project = self.controller.store.project()
        if project is not None:
            envelope["project_id"] = project.project_id
        if expected is not None:
            envelope["expected_record_version"] = expected
        return self.controller.apply_command(envelope, actor or self.controller.system_actor)

    def _move(self, task_id: str, status: str, reason: str) -> None:
        task = self.controller.store.task(task_id)
        self._cmd("task.transition", {"task_id": task_id, "to_status": status, "reason": reason},
                  expected=task.record_version)

    def _complete(self, task_id: str, snapshot_id: str | None, reason: str) -> None:
        task = self.controller.store.task(task_id)
        self._cmd("task.complete", {"task_id": task_id, "snapshot_id": snapshot_id, "reason": reason},
                  expected=task.record_version)

    # ------------------------------------------------------------------ run

    def run(self, spec: BuildToolSpec, base_snapshot_id: str) -> BuildReport:
        report = BuildReport(status="running")
        intent = self._cmd("intent.submit", {"text": spec.request_text}, actor=self.human).result
        report.intent_id = intent["intent_id"]

        suite = self._cmd("suite.admit", {"suite": spec.suite}, actor=self.human).result
        criteria = {
            "behavior": self._cmd("criterion.create", {
                "subject": "Behaves as the acceptance cases specify",
                "assessment_method": "black_box_cases",
                "parameters": {"suite_artifact_id": suite["suite_artifact_id"], "suite_digest": suite["suite_digest"]},
            }, actor=self.human).result["criterion_id"],
            "packaging": self._cmd("criterion.create", {
                "subject": f"Installs into a clean environment and `{spec.console_script} --help` runs",
                "assessment_method": "package_install", "parameters": {"console_script": spec.console_script},
            }, actor=self.human).result["criterion_id"],
        }
        if self.ruff is not None:
            criteria["static"] = self._cmd("criterion.create", {
                "subject": "No unwaived critical or high static-analysis findings",
                "assessment_method": "analyzer_verdict",
            }, actor=self.human).result["criterion_id"]
        report.criterion_ids = criteria

        def ref(name: str) -> dict[str, Any]:
            return {"id": criteria[name], "revision": 1}

        implement_criteria = [ref("behavior")] + ([ref("static")] if "static" in criteria else [])
        plan = self._cmd("plan.apply", {
            "base_plan_revision": self.controller.store.project().plan_revision,
            "summary": f"build_tool template for {spec.name}",
            "add_tasks": [
                {"key": "design", "task_type": "prepare", "title": f"Define the interface of {spec.name}",
                 "budget": BUDGET},
                {"key": "implement", "task_type": "prepare", "title": f"Implement {spec.name}",
                 "criterion_refs": implement_criteria, "depends_on": ["design"], "budget": BUDGET},
                {"key": "package", "task_type": "assess", "title": f"Package {spec.name}",
                 "criterion_refs": [ref("packaging")], "depends_on": ["implement"], "budget": BUDGET},
                {"key": "review", "task_type": "decide", "title": f"Release review for {spec.name}",
                 "depends_on": ["package"], "budget": BUDGET},
            ],
        }).result
        report.task_ids = {"planning": intent["planning_task_id"], **plan["task_ids"]}
        self._move(intent["planning_task_id"], "running", "planning")
        self._complete(intent["planning_task_id"], None, "plan revision recorded")
        self._move(report.task_ids["design"], "running", "interface defined by the admitted acceptance suite")
        self._complete(report.task_ids["design"], None, "acceptance suite admitted by the user")

        candidate = self._implement(spec, base_snapshot_id, report)
        if candidate is None:
            report.status = "blocked"
            return report
        report.final_snapshot_id = candidate

        self._move(report.task_ids["package"], "running", "packaging check")
        package = self.assessment.assess_package(criteria["packaging"], candidate)
        report.package = package
        if package["result"] != "pass":
            report.status, report.blocker = "blocked", f"packaging: {package.get('reason', package['result'])}"
            self._move(report.task_ids["package"], "waiting_for_user", report.blocker)
            return report
        self._complete(report.task_ids["package"], candidate, "packaging criterion passed")

        review_task = report.task_ids["review"]
        self._move(review_task, "running", "awaiting the user's release decision")
        task = self.controller.store.task(review_task)
        report.decision_id = self._cmd("decision.request", {
            "decision_kind": "release", "subject_ref": {"id": review_task, "revision": task.record_version},
            "question": f"Release {spec.name} built from snapshot {candidate}?",
        }).result["decision_id"]
        report.status = "awaiting_review"
        return report

    def _implement(self, spec: BuildToolSpec, base_snapshot_id: str, report: BuildReport) -> str | None:
        task_id = report.task_ids["implement"]
        self._move(task_id, "running", "implementation started")
        slot = SlotContract(
            slot_id="write_files", revision=1, output_model=FilesProposal,
            instructions=("You write complete files for a small Python tool. Reply with JSON only: "
                          '{"files": [{"path": ..., "content": ...}], "rationale": ..., "record_ids": [...]}. '
                          "Only edit paths under: " + ", ".join(spec.allowed_paths)),
            validators=(paths_within(spec.allowed_paths), python_parses, cites_known_records()),
        )
        failures: list[str] = []
        seen: set[str] = set()
        for attempt in range(1 + spec.max_repairs):
            bundle = self.context.build(task_id, base_snapshot_id, budget_tokens=spec.context_budget_tokens,
                                        intent_text=spec.request_text)
            prompt = bundle.render()
            if failures:
                prompt += ("\n\nYour previous candidate failed these checks (locations only; expected answers "
                           "are hidden):\n- " + "\n- ".join(failures[:20]))
                report.repair_prompts.append(prompt)
            context = SlotContext(allowed_record_ids={task_id, *report.criterion_ids.values()})
            result = run_slot(self.gateway, slot, context, prompt, model_config_id=self.model_config_id,
                              task_id=task_id)
            if result.status != "valid":
                report.blocker = f"model could not produce a valid proposal: {result.reason or result.errors[:1]}"
                self._move(task_id, "waiting_for_user", report.blocker[:1900])
                return None
            files = {item["path"]: item["content"].encode("utf-8") for item in result.output["files"]}
            candidate = create_candidate(self.controller, base_snapshot_id, files)
            if candidate["manifest_digest"] in seen:
                report.blocker = "no_progress: the repair produced an identical candidate"
                self._move(task_id, "waiting_for_user", report.blocker)
                return None
            seen.add(candidate["manifest_digest"])
            snapshot_id = candidate["snapshot_id"]
            report.candidates.append(snapshot_id)
            behavior = self.assessment.assess_black_box(report.criterion_ids["behavior"], snapshot_id)
            report.assessments.append({"criterion": "behavior", "snapshot": snapshot_id, "result": behavior["result"]})
            failures = [f"case {case.case_id}: {'; '.join(case.reasons)}"
                        for case in behavior["run"].cases if case.outcome != "pass"] if "run" in behavior else []
            static_ok = True
            if self.ruff is not None:
                static = self.assessment.assess_analyzer(report.criterion_ids["static"], snapshot_id, self.ruff)
                report.assessments.append({"criterion": "static", "snapshot": snapshot_id, "result": static["result"]})
                static_ok = static["result"] == "pass"
                if not static_ok:
                    failures += [f"{f.path}:{f.line} {f.rule_id} ({f.severity}): {f.message}"
                                 for f in static.get("findings") or [] if f.severity in ("critical", "high")]
            if behavior["result"] == "pass" and static_ok:
                self._complete(task_id, snapshot_id, f"candidate {snapshot_id} passed its criteria")
                return snapshot_id
        report.blocker = f"repair budget exhausted after {1 + spec.max_repairs} candidates"
        # A person decides what happens next (more budget, a new approach, or
        # stopping); the acceptance threshold is never lowered to finish.
        self._move(task_id, "waiting_for_user", report.blocker)
        return None

    def finish_review(self, report: BuildReport) -> None:
        """After the user records the release decision, complete the review task."""
        self._complete(report.task_ids["review"], None, "release decision recorded by the user")
        report.status = "completed"
