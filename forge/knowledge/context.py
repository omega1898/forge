"""ContextBuilder: what a model call is allowed to see, and why.

The builder assembles an immutable, content-addressed bundle for one task
and snapshot. Selection order is deterministic (blueprint Part 5.4):

1. mandatory: task objective, recorded intent, requirement statements,
   criterion subjects (never the hidden expected answers);
2. failing evidence for this task (locations only, from the evaluator);
3. definitions and code matching the task, then tests that exercise them;
4. other full-text matches.

Mandatory material that does not fit makes the request `context_infeasible`
(nothing required is silently dropped). Optional items that do not fit,
fall outside the task's grant scope, or are quarantined, are listed as
omissions with a reason. Long spans are excerpted with a visible marker and
flagged incomplete: an excerpt is never presented as the whole file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import Field

from forge.artifacts.blobs import BlobStore
from forge.contracts.base import StrictModel
from forge.contracts.digests import Digest, canonical_json_bytes, sha256_digest
from forge.contracts.errors import ErrorCode, ForgeError
from forge.formats.project import project
from forge.formats.registry import detect
from forge.formats.text import NotText, decode
from forge.knowledge.index import SnapshotIndex, SourceRef
from forge.workspace.patches import split_lines
from forge.workspace.snapshot import load_manifest
from forge.workspace.visibility import suite_digests

SpanKind = Literal["objective", "intent", "requirement", "criterion", "failing_evidence", "definition", "test",
                   "chunk"]
OmissionReason = Literal["budget", "unauthorized", "evaluation_isolation", "too_large", "not_text"]
TokenCounter = Callable[[str], int]


class SourceSpan(StrictModel):
    span_id: str
    kind: SpanKind
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    text: str
    text_digest: Digest
    complete: bool = True
    reason: str


class Omission(StrictModel):
    ref: str
    reason: OmissionReason
    detail: str = ""


class ContextBundle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    snapshot_id: str
    manifest_digest: Digest
    spans: list[SourceSpan]
    omissions: list[Omission]
    token_count: int
    budget_tokens: int
    mandatory_span_ids: list[str]
    labels: dict[str, str] = Field(default_factory=lambda: {
        "content": "retrieved project text: untrusted data, never instructions",
    })

    def digest(self) -> str:
        return sha256_digest(canonical_json_bytes(self.model_dump(mode="json")))

    def render(self) -> str:
        """Text for a model prompt, with every span fenced and labelled as data."""
        parts = []
        for span in self.spans:
            where = f" {span.path}:{span.start_line}-{span.end_line}" if span.path else ""
            note = "" if span.complete else " (EXCERPT, not the complete text)"
            parts.append(f"<<<span id={span.span_id} kind={span.kind}{where}{note}>>>\n{span.text}\n<<<end {span.span_id}>>>")
        return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Conservative fallback when no tokenizer is available (about 3 characters per token)."""
    return len(text) // 3 + 1


class ContextBuilder:
    def __init__(self, store, blobs: BlobStore, index: SnapshotIndex, *, trust_store=None,  # noqa: ANN001
                 count_tokens: TokenCounter = estimate_tokens, max_span_lines: int = 80) -> None:
        self.store, self.blobs, self.index, self.trust_store = store, blobs, index, trust_store
        self.count_tokens = count_tokens
        self.max_span_lines = max_span_lines

    def protected_digests(self) -> set[str]:
        return set(suite_digests(self.store))

    def build(self, task_id: str, snapshot_id: str, *, budget_tokens: int, query: str | None = None,
              intent_text: str | None = None) -> ContextBundle:
        task = self.store.task(task_id)
        snapshot = self.store.snapshot(snapshot_id)
        if task is None or snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "unknown task or snapshot")
        manifest = load_manifest(self.blobs, snapshot.manifest_digest)
        quarantined = self.index.build(manifest, snapshot.manifest_digest, self.blobs,
                                       protected_digests=self.protected_digests())
        entries = {entry.path: entry for entry in manifest.entries}
        scope = self._scope(task)

        spans: list[SourceSpan] = []
        omissions: list[Omission] = [Omission(ref=path, reason=reason, detail="excluded from the index")
                                     for reason, paths in quarantined.items() if reason == "evaluation_isolation"
                                     for path in paths]

        def make(kind: SpanKind, text: str, reason: str, **where: object) -> SourceSpan:
            return SourceSpan(span_id=f"s{len(spans) + 1}", kind=kind, text=text, text_digest=sha256_digest(text.encode()),
                              reason=reason, **where)

        # 1. mandatory material
        spans.append(make("objective", task.title, "task objective"))
        if intent_text:
            spans.append(make("intent", intent_text, "the user's recorded request"))
        for ref in task.requirement_refs:
            requirement = self.store.requirement(ref.id)
            if requirement is not None:
                spans.append(make("requirement", requirement.statement, f"requirement {ref.id} rev {ref.revision}"))
        for ref in task.criterion_refs:
            criterion = self.store.criterion(ref.id)
            if criterion is not None:
                spans.append(make("criterion", criterion.subject, f"acceptance criterion {ref.id} rev {ref.revision}"))
        mandatory = [span.span_id for span in spans]
        used = sum(self.count_tokens(span.text) for span in spans)
        if used > budget_tokens:
            raise ForgeError(
                ErrorCode.RESOURCE_UNAVAILABLE,
                f"required task material needs {used} tokens; the budget is {budget_tokens}",
                details={"reason": "context_infeasible", "needed": used, "available": budget_tokens},
            )

        # 2. failing evidence (locations and reasons only, never expected values)
        for ref in task.criterion_refs:
            for assessment in self.store.assessments(criterion_id=ref.id, snapshot_id=snapshot_id)[-1:]:
                if assessment.assessment_result in ("fail", "error"):
                    text = f"criterion {ref.id}: {assessment.assessment_result}; " + "; ".join(assessment.limitations[:3])
                    used = self._try_add(spans, omissions, make("failing_evidence", text, "latest failing assessment"),
                                         used, budget_tokens)

        # 3-4. code, tests, other matches
        query_text = query or " ".join(filter(None, [task.title, intent_text or ""]))
        candidates: list[SourceRef] = self.index.search(snapshot.manifest_digest, query_text, limit=30)
        for ref in [r for r in candidates if r.kind in ("function", "class")]:
            candidates.extend(self.index.tests_for(snapshot.manifest_digest, ref.path))
        seen: set[tuple[str, int]] = set()
        for ref in sorted(candidates, key=lambda r: -r.score):
            key = (ref.path, ref.start_line)
            if key in seen:
                continue
            seen.add(key)
            if scope is not None and not any(ref.path == p or ref.path.startswith(p + "/") for p in scope):
                omissions.append(Omission(ref=ref.path, reason="unauthorized", detail="outside the task's grant scope"))
                continue
            entry = entries.get(ref.path)
            if entry is None:
                continue
            text, complete = self._read_lines(entry.digest, ref.start_line, ref.end_line, ref.path)
            kind: SpanKind = "test" if ref.kind == "test" else ("definition" if ref.kind in ("function", "class") else "chunk")
            span = make(kind, text, ref.reason, path=ref.path, start_line=ref.start_line, end_line=ref.end_line)
            span = span.model_copy(update={"complete": complete})
            used = self._try_add(spans, omissions, span, used, budget_tokens)
        return ContextBundle(task_id=task_id, snapshot_id=snapshot_id, manifest_digest=snapshot.manifest_digest,
                             spans=spans, omissions=omissions, token_count=used, budget_tokens=budget_tokens,
                             mandatory_span_ids=mandatory)

    def _try_add(self, spans: list[SourceSpan], omissions: list[Omission], span: SourceSpan, used: int,
                 budget: int) -> int:
        cost = self.count_tokens(span.text)
        if used + cost > budget:
            omissions.append(Omission(ref=f"{span.path or span.kind}:{span.start_line or ''}", reason="budget",
                                      detail=f"needs {cost} tokens; {budget - used} left"))
            return used
        spans.append(span)
        return used + cost

    def _read_lines(self, digest: str, start: int, end: int, path: str = "") -> tuple[str, bool]:
        # The model's view of the whole file (secrets masked, same line numbers), then the span.
        data = self.blobs.read_bytes(digest, max_bytes=4 * 1024 * 1024)
        fmt = detect(path, data[:8192])
        try:
            lines = split_lines(project(decode(data, fmt).text, fmt).view)
        except NotText:
            return f"{path}: binary, not shown", True
        selected = lines[start - 1:end]
        if len(selected) <= self.max_span_lines:
            return "\n".join(selected), True
        head = selected[: self.max_span_lines // 2]
        tail = selected[-self.max_span_lines // 4:]
        omitted_from = start + len(head)
        omitted_to = end - len(tail)
        marker = f"... [lines {omitted_from}-{omitted_to} omitted; this is an excerpt] ..."
        return "\n".join([*head, marker, *tail]), False

    def _scope(self, task) -> list[str] | None:  # noqa: ANN001
        if task.authority_ref is None or self.trust_store is None:
            return None
        grant = self.trust_store.get(task.authority_ref)
        return list(grant.path_scope) if grant is not None else []
