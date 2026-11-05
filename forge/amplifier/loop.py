"""TaskAssistant: the bounded adaptive loop (blueprint Part 5.7).

Each turn the model returns one AgentProposal. The loop:

- `observe`: runs a registered *read-only* tool against the snapshot
  (read_file, search_code, list_files) and appends a bounded observation;
- `propose_patch`: applies the patch strictly to the base snapshot, builds a
  candidate, assesses it; a pass becomes a reviewable proposal;
- `needs_user`: stops and asks;
- `complete`: stops with a suggestion; the completion evaluator decides.

Unproductive cycles are caught by fingerprinting each proposal together with
the evidence gathered so far: the same action on unchanged evidence is
`no_progress` (INT-T11). Turn and candidate budgets are fixed by the
caller; exhausting them returns the best state and a blocker, never a
lowered bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.contracts.digests import digest_json
from forge.gateway.contracts import Message
from forge.gateway.proposals import AgentProposal
from forge.gateway.schema_subset import flat_schema
from forge.workspace.candidates import create_candidate
from forge.workspace.patches import apply_file_patch, parse_patch
from forge.workspace.snapshot import load_manifest

READ_TOOLS = ("read_file", "search_code", "list_files")
MAX_OBSERVATION = 2000


@dataclass
class AssistReport:
    status: str
    turns: int = 0
    observations: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    proposal_id: str | None = None
    proposal_digest: str | None = None
    detail: str = ""
    # Filled whenever the loop stops without a result: why, in plain terms (see forge.gateway.escalation).
    gap: list[str] = field(default_factory=list)


class TaskAssistant:
    def __init__(self, controller, gateway, model_config_id: str, index, assessment, *,  # noqa: ANN001
                 observe_turns: int = 8, max_candidates: int = 2) -> None:
        self.controller, self.gateway, self.model_config_id = controller, gateway, model_config_id
        self.index, self.assessment = index, assessment
        self.observe_turns, self.max_candidates = observe_turns, max_candidates

    def _files(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self.controller.store.snapshot(snapshot_id)
        return {entry.path: entry for entry in load_manifest(self.controller.blobs, snapshot.manifest_digest).entries}

    def _read(self, digest: str) -> str:
        return self.controller.blobs.read_bytes(digest, max_bytes=2 * 1024 * 1024).decode("utf-8", "replace")

    def _observe(self, proposal: AgentProposal, snapshot_id: str) -> str:
        files = self._files(snapshot_id)
        args = proposal.tool_arguments
        if proposal.tool_id == "list_files":
            return "\n".join(sorted(files))[:MAX_OBSERVATION]
        if proposal.tool_id == "read_file" and args and args[0] in files:
            return self._read(files[args[0]].digest)[:MAX_OBSERVATION]
        if proposal.tool_id == "search_code" and args:
            digest = self.controller.store.snapshot(snapshot_id).manifest_digest
            self.index.build(load_manifest(self.controller.blobs, digest), digest, self.controller.blobs)
            refs = self.index.search(digest, " ".join(args), limit=10)
            return "\n".join(f"{r.path}:{r.start_line} {r.kind} {r.name}" for r in refs)[:MAX_OBSERVATION]
        return f"tool {proposal.tool_id!r} with these arguments is not available (read-only tools: {', '.join(READ_TOOLS)})"

    def run(self, task_id: str, base_snapshot_id: str, criterion_id: str, objective: str) -> AssistReport:
        report = self._run(task_id, base_snapshot_id, criterion_id, objective)
        if report.status != "ready_for_review":
            from forge.gateway.escalation import explain_gap

            report.gap = explain_gap(report)
        return report

    def try_proposal(self, proposal: AgentProposal, task_id: str, base_snapshot_id: str,
                     criterion_id: str) -> AssistReport:
        """Verify a proposal from elsewhere (for example an escalated frontier answer) exactly like a local one."""
        report = AssistReport(status="running")
        outcome = self._try_patch(proposal, task_id, base_snapshot_id, criterion_id, report)
        report.status = "ready_for_review" if outcome == "ready" else "rejected"
        if outcome != "ready":
            report.detail = "; ".join(outcome)[:1000]
        return report

    def _run(self, task_id: str, base_snapshot_id: str, criterion_id: str, objective: str) -> AssistReport:
        report = AssistReport(status="running")
        seen: set[str] = set()
        failures: list[str] = []
        for turn in range(1, self.observe_turns + self.max_candidates + 1):
            report.turns = turn
            prompt = (f"Objective: {objective}\nAllowed observation tools: {', '.join(READ_TOOLS)}.\n"
                      + "".join(f"\nObservation {n}:\n{text}" for n, text in enumerate(report.observations, 1))
                      + ("\nFailing checks on your last candidate:\n- " + "\n- ".join(failures) if failures else ""))
            result = self.gateway.generate(model_config_id=self.model_config_id, slot_id="next_step",
                                           messages=[Message(role="user", content=prompt)], output_model=AgentProposal,
                                           output_schema=flat_schema(AgentProposal), task_id=task_id)
            if result.status != "valid":
                report.observations.append(f"the previous reply was rejected: {result.status}")
                continue
            proposal = AgentProposal.model_validate(result.output)
            # Evidence is the set of DISTINCT observations: the same failure
            # message appended again is not new information.
            fingerprint = digest_json({"proposal": result.output, "evidence": sorted(set(report.observations)),
                                       "failures": sorted(set(failures))})
            if fingerprint in seen:
                report.status, report.detail = "no_progress", f"repeated {proposal.action} with no new evidence"
                return report
            seen.add(fingerprint)
            if proposal.action == "observe":
                report.observations.append(self._observe(proposal, base_snapshot_id))
            elif proposal.action == "propose_patch":
                outcome = self._try_patch(proposal, task_id, base_snapshot_id, criterion_id, report)
                if outcome == "ready":
                    report.status = "ready_for_review"
                    return report
                failures = outcome
                if len(report.candidates) >= self.max_candidates:
                    report.status, report.detail = "budget_exhausted", "candidate budget used; latest failures kept"
                    return report
            elif proposal.action == "needs_user":
                report.status, report.detail = "waiting_for_user", proposal.question or ""
                return report
            elif proposal.action == "complete":
                report.status = "suggested_complete"
                return report
            else:
                report.observations.append(f"action {proposal.action} is not handled by this loop")
        report.status, report.detail = "budget_exhausted", "turn budget used"
        return report

    def _try_patch(self, proposal: AgentProposal, task_id: str, base_snapshot_id: str, criterion_id: str,
                   report: AssistReport):  # noqa: ANN202
        files = self._files(base_snapshot_id)
        try:
            patches = parse_patch(proposal.patch or "")
            new_files = {}
            deletions = []
            for patch in patches:
                base_text = self._read(files[patch.path].digest) if patch.path in files else None
                text = apply_file_patch(base_text, patch)
                if text is None:
                    deletions.append(patch.path)
                else:
                    new_files[patch.path] = text.encode("utf-8")
        except Exception as error:  # noqa: BLE001 - any patch problem is reported back, nothing is built
            report.observations.append(f"patch rejected: {str(error)[:300]}")
            return [f"patch rejected: {str(error)[:300]}"]
        candidate = create_candidate(self.controller, base_snapshot_id, new_files, deletions=tuple(deletions))
        report.candidates.append(candidate["snapshot_id"])
        outcome = self.assessment.assess_black_box(criterion_id, candidate["snapshot_id"])
        if outcome["result"] == "pass":
            created = self.controller.apply_command(
                {"schema_version": "1.0", "method": "proposal.create",
                 "project_id": self.controller.store.project().project_id,
                 "idempotency_key": f"proposal-{candidate['snapshot_id']}",
                 "params": {"task_id": task_id, "base_snapshot_id": base_snapshot_id,
                            "candidate_snapshot_id": candidate["snapshot_id"]}},
                self.controller.system_actor,
            ).result
            report.proposal_id, report.proposal_digest = created["proposal_id"], created["proposal_digest"]
            return "ready"
        return [f"case {c.case_id}: {'; '.join(c.reasons)}" for c in outcome["run"].cases if c.outcome != "pass"] \
            if "run" in outcome else [outcome["result"]]
