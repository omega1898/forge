"""TaskAssistant: the bounded adaptive loop (blueprint Part 5.7).

Each turn the model returns one AgentProposal. The loop:

- `observe`: runs a registered *read-only* tool against the snapshot
  (read_file, search_code, list_files) and appends a bounded observation;
- `propose_patch`: applies the patch strictly to the base snapshot, builds a
  candidate, assesses it; a pass becomes a reviewable proposal, unless the
  optional quality gate finds new coding-standard violations, in which case
  the model gets one repair brief per violation and tries again;
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
from forge.contracts.errors import ForgeError
from forge.formats import views
from forge.formats.checks import Problem, check, new_problems
from forge.formats.project import Projection, project, restore
from forge.formats.registry import detect
from forge.formats.text import decode, encode
from forge.gateway.contracts import Message
from forge.gateway.proposals import AgentProposal
from forge.gateway.schema_subset import flat_schema
from forge.workspace import visibility
from forge.workspace.candidates import create_candidate
from forge.workspace.patches import FilePatch, apply_file_patch, parse_patch
from forge.workspace.snapshot import load_manifest

READ_TOOLS = ("read_file", "search_code", "list_files")
MAX_OBSERVATION = 2000
MAX_EDITABLE = 2 * 1024 * 1024    # a text file larger than this is not patched
MAX_VIEWED = 16 * 1024 * 1024     # bytes a reader may look at to summarize a document or binary


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
                 observe_turns: int = 8, max_candidates: int = 2, quality_gate=None) -> None:  # noqa: ANN001
        self.controller, self.gateway, self.model_config_id = controller, gateway, model_config_id
        self.index, self.assessment = index, assessment
        self.observe_turns, self.max_candidates = observe_turns, max_candidates
        # forge.quality.guard.QualityGate: a candidate that passes its tests must also add no blocking
        # coding-standard finding; its repair briefs go back to the model as the next turn's failures.
        self.quality_gate = quality_gate

    def _files(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self.controller.store.snapshot(snapshot_id)
        return {entry.path: entry for entry in load_manifest(self.controller.blobs, snapshot.manifest_digest).entries}

    def _edit(self, patch: FilePatch, entry: Any) -> tuple[bytes | None, list[Problem], str]:  # noqa: ANN401
        """Apply one file's patch through its format: (new bytes or None for a deletion, new problems, note).

        The patch was written against the model's view (secrets masked, same lines), so it is applied to that
        view, masked lines are restored, and the text is written back in the file's own encoding, byte order
        mark and line endings. Raises ValueError (or ForgeError) with a reason the model can act on.
        """
        base = None if entry is None else self.controller.blobs.read_bytes(entry.digest, max_bytes=MAX_EDITABLE)
        fmt = detect(patch.path, (base or b"")[:8192])
        if fmt.sensitive:
            raise ValueError(f"{patch.path} is a {fmt.title}, which FORGE never shows or edits")
        if fmt.kind != "text":
            raise ValueError(f"{patch.path} is a {fmt.title}, which is not edited as text; ask the person to change "
                             "it, or change the text sources it is made from")
        if base is None:
            edited = apply_file_patch(None, patch)
            data = encode(restore(edited or "", Projection(view="")), None, fmt)
            before = None
        else:
            decoded = decode(base, fmt)
            projection = project(decoded.text, fmt)
            edited = apply_file_patch(projection.view, patch)
            if edited is None:
                return None, [], ""
            data = encode(restore(edited, projection), decoded, fmt)
            before = check(fmt, decoded, patch.path)
        after = check(fmt, decode(data, fmt), patch.path)
        note = f"{patch.path}: {after.reason}" if after.status == "not_checked" and fmt.check else ""
        return data, new_problems(before, after), note

    def _observe(self, proposal: AgentProposal, snapshot_id: str) -> str:
        """Read-only tools, all behind one filter: the protected evaluation is never shown (visibility.py)."""
        files = self._files(snapshot_id)
        suites = visibility.suite_digests(self.controller.store)
        visible = {path: entry for path, entry in files.items()
                   if not visibility.is_hidden(path, entry.digest, suites)}
        args = proposal.tool_arguments
        if proposal.tool_id == "list_files":
            return "\n".join(sorted(visible))[:MAX_OBSERVATION]
        if proposal.tool_id == "read_file" and args and args[0] in files:
            if args[0] not in visible:
                return f"{args[0]}: {visibility.HIDDEN_NOTICE}"
            if args[0] == visibility.PROJECT_FILE:
                data = self.controller.blobs.read_bytes(files[args[0]].digest, max_bytes=2 * 1024 * 1024)
                return visibility.project_view(data)[:MAX_OBSERVATION]
            # Every other file through its format: text with secrets masked, documents and binaries summarized.
            head, size, complete = self.controller.blobs.read_head(files[args[0]].digest, max_bytes=MAX_VIEWED)
            return views.view(args[0], head, size=size, complete=complete).render(args[0])[:MAX_OBSERVATION]
        if proposal.tool_id == "search_code" and args:
            digest = self.controller.store.snapshot(snapshot_id).manifest_digest
            self.index.build(load_manifest(self.controller.blobs, digest), digest, self.controller.blobs,
                             protected_digests=set(suites))
            refs = [r for r in self.index.search(digest, " ".join(args), limit=10) if r.path in visible]
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
        except Exception as error:  # noqa: BLE001 - any patch problem is reported back, nothing is built
            report.observations.append(f"patch rejected: {str(error)[:300]}")
            return [f"patch rejected: {str(error)[:300]}"]
        protected = sorted({patch.path for patch in patches if visibility.is_protected(patch.path)})
        if protected:
            # Tests and evaluator files define success; an implementation change may not edit them.
            message = (f"patch rejected: it changes {protected}, which define what correct means. Change the "
                       "implementation only. If a test looks wrong, stop and ask the person (needs_user).")
            report.observations.append(message)
            return [message]
        new_files: dict[str, bytes] = {}
        deletions: list[str] = []
        problems: list[str] = []
        try:
            for patch in patches:
                data, found, note = self._edit(patch, files.get(patch.path))
                if data is None:
                    deletions.append(patch.path)
                else:
                    new_files[patch.path] = data
                problems += [p.render(patch.path) for p in found if p.severity == "error"]
                report.observations += [f"format warning: {p.render(patch.path)}" for p in found
                                        if p.severity == "warning"] + ([note] if note else [])
        except Exception as error:  # noqa: BLE001 - any patch problem is reported back, nothing is built
            report.observations.append(f"patch rejected: {str(error)[:300]}")
            return [f"patch rejected: {str(error)[:300]}"]
        if problems:
            # The syntax gate: a file the edit leaves unparseable is never built or tested.
            message = "patch rejected: the edited files no longer parse:\n" + "\n".join(problems[:10])
            report.observations.append(message[:1500])
            return problems[:10]
        candidate = create_candidate(self.controller, base_snapshot_id, new_files, deletions=tuple(deletions))
        report.candidates.append(candidate["snapshot_id"])
        outcome = self.assessment.assess_black_box(criterion_id, candidate["snapshot_id"])
        if outcome["result"] == "pass" and self.quality_gate is not None:
            gate = self.quality_gate.review(base_snapshot_id, candidate["snapshot_id"])
            if not gate.passed:
                report.observations.append(f"quality gate rejected the candidate: {len(gate.new)} new "
                                           "coding-standard violation(s)" if gate.new else gate.feedback[0][:300])
                return gate.feedback
        if outcome["result"] == "pass":
            try:
                created = self.controller.apply_command(
                    {"schema_version": "1.0", "method": "proposal.create",
                     "project_id": self.controller.store.project().project_id,
                     "idempotency_key": f"proposal-{candidate['snapshot_id']}",
                     "params": {"task_id": task_id, "base_snapshot_id": base_snapshot_id,
                                "candidate_snapshot_id": candidate["snapshot_id"]}},
                    self.controller.system_actor,
                ).result
            except ForgeError as error:
                # A refusal is feedback for the next turn, never a crash of the loop.
                message = f"the candidate passed its tests but could not become a proposal: {error.message}"[:500]
                report.observations.append(message)
                return [message]
            report.proposal_id, report.proposal_digest = created["proposal_id"], created["proposal_digest"]
            return "ready"
        return [f"case {c.case_id}: {'; '.join(c.reasons)}" for c in outcome["run"].cases if c.outcome != "pass"] \
            if "run" in outcome else [outcome["result"]]
