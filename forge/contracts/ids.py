"""Prefixed identifiers such as task_<uuid> and job_<uuid>.

A prefix lets a reader (and the validator) see what kind of object an ID names.
A UUID distinguishes objects; it does not prove authorization or ordering.
Ordering inside a project comes from the event sequence number instead.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from pydantic import AfterValidator, WithJsonSchema

from forge.contracts.errors import ErrorCode, ForgeError, safe_preview

# Prefixes are lowercase letters only (no underscores) so that splitting on the
# first underscore is unambiguous.
ID_PREFIXES: frozenset[str] = frozenset(
    {
        "actor",
        "artifact",
        "assessment",
        "command",
        "controller",
        "criterion",
        "decision",
        "event",
        "evidence",
        "experiment",
        "finding",
        "grant",
        "intent",
        "job",
        "project",
        "proposal",
        "request",
        "requirement",
        "snapshot",
        "task",
    }
)

_UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ID_RE = re.compile(rf"^(?P<prefix>[a-z]+)_(?P<uuid>{_UUID_TEXT})$")


def id_pattern(prefix: str) -> str:
    """Regular expression (as text) matching IDs with the given prefix."""
    return rf"^{prefix}_{_UUID_TEXT}$"


def new_id(prefix: str) -> str:
    """Create a fresh random identifier such as 'task_3f2c...'."""
    if prefix not in ID_PREFIXES:
        raise ValueError(f"unknown identifier prefix {prefix!r}")
    return f"{prefix}_{uuid.uuid4()}"


def parse_id(value: object, expected_prefix: str | None = None) -> tuple[str, uuid.UUID]:
    """Split an identifier into (prefix, uuid), raising a clear error if invalid."""
    if not isinstance(value, str):
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"identifier must be a string, got {type(value).__name__}",
        )
    match = _ID_RE.match(value)
    wanted = f"{expected_prefix}_<uuid>" if expected_prefix else "<prefix>_<uuid>"
    if match is None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"invalid identifier {safe_preview(value)}: expected {wanted} "
            "with a lowercase hyphenated UUID",
        )
    prefix = match.group("prefix")
    if prefix not in ID_PREFIXES:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"invalid identifier {safe_preview(value)}: unknown prefix {prefix!r}",
        )
    if expected_prefix is not None and prefix != expected_prefix:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"invalid identifier {safe_preview(value)}: expected a "
            f"{expected_prefix!r} identifier, got a {prefix!r} identifier",
        )
    return prefix, uuid.UUID(match.group("uuid"))


def id_prefix(value: str) -> str:
    return parse_id(value)[0]


def _validator(prefix: str | None):
    def check(value: str) -> str:
        try:
            parse_id(value, prefix)
        except ForgeError as error:
            # Pydantic turns ValueError into a structured validation error.
            raise ValueError(error.message) from None
        return value

    return check


def IdOf(prefix: str):  # noqa: N802 - reads like a type in annotations
    """Annotated string type accepting only identifiers with this prefix."""
    if prefix not in ID_PREFIXES:
        raise ValueError(f"unknown identifier prefix {prefix!r}")
    return Annotated[
        str,
        AfterValidator(_validator(prefix)),
        WithJsonSchema({"type": "string", "pattern": id_pattern(prefix)}),
    ]


AnyId = Annotated[
    str,
    AfterValidator(_validator(None)),
    WithJsonSchema({"type": "string", "pattern": rf"^[a-z]+_{_UUID_TEXT}$"}),
]

ProjectId = IdOf("project")
TaskId = IdOf("task")
CriterionId = IdOf("criterion")
RequirementId = IdOf("requirement")
DecisionId = IdOf("decision")
GrantId = IdOf("grant")
ActorId = IdOf("actor")
CommandId = IdOf("command")
EventId = IdOf("event")
ControllerId = IdOf("controller")
ArtifactId = IdOf("artifact")
SnapshotId = IdOf("snapshot")
JobId = IdOf("job")
EvidenceId = IdOf("evidence")
AssessmentId = IdOf("assessment")
ProposalId = IdOf("proposal")
FindingId = IdOf("finding")
ExperimentId = IdOf("experiment")
IntentId = IdOf("intent")
