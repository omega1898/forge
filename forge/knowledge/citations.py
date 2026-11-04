"""Claim support: does a cited source mechanically back a statement?

A ClaimSupport links a statement to a span in the context bundle. The
citation checker verifies what code can verify:

- the span exists and was actually available to the call (in the bundle);
- the quoted text appears in that span byte-for-byte;
- the span belongs to the snapshot the bundle was built from.

Whether the passage *supports* the statement is a separate semantic
judgment. A real citation can be irrelevant or even contradict the claim
(INT-T06), so semantic support stays `unresolved` until a reviewer or an
evaluated check decides. Missing support yields `inconclusive`, never a
fabricated citation.
"""

from __future__ import annotations

from typing import Literal

from forge.contracts.base import StrictModel
from forge.knowledge.context import ContextBundle


class ClaimSupport(StrictModel):
    statement: str
    span_id: str | None
    quote: str = ""
    kind: Literal["quotation", "tool_observation", "derived_fact", "interpretation"] = "quotation"


class CitationCheck(StrictModel):
    statement: str
    mechanical: Literal["ok", "failed", "inconclusive"]
    problems: list[str]
    semantic_support: Literal["unresolved"] = "unresolved"


def check_claims(claims: list[ClaimSupport], bundle: ContextBundle) -> list[CitationCheck]:
    spans = {span.span_id: span for span in bundle.spans}
    results = []
    for claim in claims:
        problems: list[str] = []
        if claim.span_id is None:
            results.append(CitationCheck(statement=claim.statement, mechanical="inconclusive",
                                         problems=["no source cited"]))
            continue
        span = spans.get(claim.span_id)
        if span is None:
            problems.append(f"span {claim.span_id} was not available to this call")
        elif claim.kind == "quotation" and (not claim.quote or claim.quote not in span.text):
            problems.append("quoted text does not appear verbatim in the cited span")
        results.append(CitationCheck(statement=claim.statement, mechanical="failed" if problems else "ok",
                                     problems=problems))
    return results
