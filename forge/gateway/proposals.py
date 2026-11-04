"""What a model may propose (blueprint Part 5.7).

One flat object with an `action` field, rather than a union of object types:
grammar-constrained decoding handles enums and optional fields well, and
unions poorly. Pydantic then enforces the per-action rules after generation.

Every proposal is untrusted. `complete` is a suggestion to assess
completion, never a command; there is no action that grants authority,
approves anything, or edits the record directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from forge.contracts.base import StrictModel

Action = Literal["observe", "propose_plan", "propose_patch", "request_experiment", "needs_user", "complete"]


class AgentProposal(StrictModel):
    action: Action
    record_ids: list[str] = Field(default_factory=list, max_length=32)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_result: str = Field(default="", max_length=1000)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    # action-specific fields (each required only for its action)
    tool_id: str | None = Field(default=None, max_length=64)
    tool_arguments: list[str] = Field(default_factory=list, max_length=32)
    patch: str | None = Field(default=None, max_length=256 * 1024)
    affected_paths: list[str] = Field(default_factory=list, max_length=64)
    hypothesis: str | None = Field(default=None, max_length=1000)
    question: str | None = Field(default=None, max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _fields_for_action(self) -> AgentProposal:
        required = {
            "observe": ("tool_id",),
            "propose_patch": ("patch",),
            "request_experiment": ("hypothesis",),
            "needs_user": ("question",),
        }.get(self.action, ())
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"action {self.action} requires: {', '.join(missing)}")
        if self.action == "propose_patch" and not self.affected_paths:
            raise ValueError("a patch proposal must list its affected paths")
        return self
