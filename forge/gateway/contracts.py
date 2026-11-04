"""Model gateway contracts: configurations, capabilities, requests, results.

A model *name* does not identify an experiment. A ModelConfig records
everything that changes behavior (runtime, chat template, quantization,
context size, generation defaults); its digest is the identity that probes
admit. Change the runtime or template under the same model name and the
digest changes, so the configuration must be probed again.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from forge.contracts.base import Slug, StrictModel
from forge.contracts.digests import Digest, digest_json

CapabilityState = Literal["declared", "observed", "unverified", "failed"]
CallStatus = Literal[
    "valid",
    "malformed_json",
    "schema_violation",
    "unsupported_action",
    "truncated",
    "context_infeasible",
    "refusal",
    "timeout",
    "cancelled",
    "cancel_unconfirmed",
    "transport_error",
    "policy_rejected",
]


class ModelConfig(StrictModel):
    model_config_id: str
    provider_type: Literal["fake", "openai_compatible"]
    endpoint: str | None = None
    model_name: str
    weights_digest: Digest | None = None
    tokenizer_digest: Digest | None = None
    chat_template_digest: Digest | None = None
    quantization: str | None = None
    runtime_version: str
    context_capacity: int = Field(ge=256)
    max_output_tokens: int = Field(ge=16)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    probe_suite_version: int = 1

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


class CapabilityReport(StrictModel):
    config_digest: Digest
    capabilities: dict[str, CapabilityState]
    notes: list[str] = Field(default_factory=list)

    def admits(self, required: list[str]) -> list[str]:
        """Required capabilities that were NOT observed."""
        return [name for name in required if self.capabilities.get(name) != "observed"]


class Message(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=2_000_000)


class GenerationRequest(StrictModel):
    call_id: str
    task_id: str | None
    model_config_id: str
    slot_id: Slug
    messages: list[Message] = Field(min_length=1)
    output_schema: dict[str, JsonValue] | None
    max_output_tokens: int = Field(ge=1)
    deadline_ms: int = Field(ge=1, le=3_600_000)


class TokenCount(StrictModel):
    count: int = Field(ge=0)
    mode: Literal["exact_template", "text_only", "estimated"]
    tokenizer_id: str


class RawGeneration(StrictModel):
    """What an adapter returns, before the gateway validates anything."""

    text: str
    finished: bool                        # False: the stream ended mid-message
    finish_reason: str | None = None
    prompt_tokens: int | None = None      # None means unknown, never estimated
    completion_tokens: int | None = None


class GenerationResult(StrictModel):
    call_id: str
    status: CallStatus
    output: dict[str, JsonValue] | None = None
    errors: list[str] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    wall_ms: int = 0
    attempts: int = 1
