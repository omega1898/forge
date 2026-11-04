"""Ticket F01: contract versions, ID/digest/time conventions, offline schemas.

Acceptance: a fresh installation loads schemas offline; invalid IDs and
incompatible versions fail with clear messages.
"""

from __future__ import annotations

import socket
from datetime import datetime

import jsonschema
import pytest

from forge.contracts import schemas
from forge.contracts.clock import format_timestamp, parse_timestamp
from forge.contracts.commands import parse_command
from forge.contracts.digests import canonical_json_bytes, digest_json, validate_digest
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id, parse_id
from forge.contracts.versions import check_schema_version
from tests.conftest import BUDGET, command

PROJECT = "project_00000000-0000-4000-8000-000000000001"


# ------------------------------------------------------------- identifiers --


def test_new_id_round_trips_with_its_prefix() -> None:
    task_id = new_id("task")
    prefix, _ = parse_id(task_id, "task")
    assert prefix == "task"


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("job_00000000-0000-4000-8000-000000000001", "expected a 'task' identifier"),
        ("task_00000000-0000-4000-8000-00000000000G", "expected task_<uuid>"),
        ("TASK_00000000-0000-4000-8000-000000000001", "expected task_<uuid>"),
        ("task_00000000000040008000000000000001", "expected task_<uuid>"),
        ("task-00000000-0000-4000-8000-000000000001", "expected task_<uuid>"),
        ("", "expected task_<uuid>"),
    ],
)
def test_invalid_ids_fail_with_clear_messages(value: str, fragment: str) -> None:
    with pytest.raises(ForgeError) as caught:
        parse_id(value, "task")
    assert caught.value.code == ErrorCode.INVALID_REQUEST
    assert fragment in caught.value.message


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(ForgeError, match="unknown prefix 'wizard'"):
        parse_id("wizard_00000000-0000-4000-8000-000000000001")


def test_hostile_huge_id_is_not_echoed_unbounded() -> None:
    with pytest.raises(ForgeError) as caught:
        parse_id("x" * 1_000_000, "task")
    assert len(caught.value.message) < 300


# ----------------------------------------------------------------- digests --


def test_canonical_json_ignores_key_order_and_whitespace() -> None:
    assert digest_json({"a": 1, "b": [1, 2]}) == digest_json({"b": [1, 2], "a": 1})
    assert canonical_json_bytes({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": float("nan")})


@pytest.mark.parametrize("value", ["sha256:ABC", "md5:" + "0" * 64, "sha256:" + "0" * 63, 7])
def test_invalid_digests_fail(value: object) -> None:
    with pytest.raises(ForgeError, match="expected sha256:<64 lowercase hex>"):
        validate_digest(value)


# -------------------------------------------------------------- timestamps --


def test_timestamps_normalize_to_utc_z() -> None:
    parsed = parse_timestamp("2026-11-04T10:30:00+01:30")
    assert format_timestamp(parsed) == "2026-11-04T09:00:00.000000Z"


@pytest.mark.parametrize("value", ["2026-11-04T09:00:00", "2026-11-04", "yesterday", 1730000000])
def test_timestamps_without_offset_are_rejected(value: object) -> None:
    with pytest.raises(ForgeError, match="expected UTC RFC3339"):
        parse_timestamp(value)


def test_formatting_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="without a timezone"):
        format_timestamp(datetime(2026, 11, 4))


# ---------------------------------------------------------------- versions --


@pytest.mark.parametrize("version", ["2.0", "1.1", "0.9"])
def test_incompatible_versions_fail_with_clear_messages(version: str) -> None:
    with pytest.raises(ForgeError) as caught:
        check_schema_version(version, what="command")
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert f"schema_version {version}" in caught.value.message
    assert "supports 1.0" in caught.value.message


@pytest.mark.parametrize("version", [None, "1", "v1.0", 1.0, "01.0"])
def test_malformed_versions_are_invalid_requests(version: object) -> None:
    with pytest.raises(ForgeError) as caught:
        check_schema_version(version)
    assert caught.value.code == ErrorCode.INVALID_REQUEST


def test_version_is_checked_before_shape() -> None:
    # A newer client should hear "unsupported version", not "unexpected field".
    raw = command("task.create", {"anything": True}, project_id=PROJECT)
    raw["schema_version"] = "2.0"
    raw["field_from_the_future"] = 1
    with pytest.raises(ForgeError) as caught:
        parse_command(raw)
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION


# ---------------------------------------------------------- command shapes --


def _rejects(raw: dict, fragment: str) -> None:
    with pytest.raises(ForgeError) as caught:
        parse_command(raw)
    assert caught.value.code == ErrorCode.INVALID_REQUEST
    assert fragment in caught.value.message, caught.value.message


def test_unexpected_fields_are_rejected() -> None:
    raw = command("criterion.create", {"subject": "s", "assessment_method": "test_suite"}, project_id=PROJECT)
    raw["params"]["approved"] = True
    _rejects(raw, "approved")


def test_types_are_not_coerced() -> None:
    raw = command(
        "task.create",
        {"task_type": "prepare", "title": "t", "budget": {"max_wall_ms": "60000", "max_attempts": 1}},
        project_id=PROJECT,
    )
    _rejects(raw, "max_wall_ms")


def test_project_create_must_not_choose_its_own_id() -> None:
    _rejects(command("project.create", {"name": "n", "objective": "o"}, project_id=PROJECT), "assigns it")


def test_mutations_of_existing_records_require_expected_version() -> None:
    raw = command(
        "task.transition",
        {"task_id": new_id("task"), "to_status": "cancelled", "reason": "r"},
        project_id=PROJECT,
    )
    _rejects(raw, "requires expected_record_version")


def test_control_characters_are_rejected_in_text() -> None:
    raw = command("project.create", {"name": "ok\x1b[2J", "objective": "o"})
    _rejects(raw, "control characters")


def test_excessive_budgets_are_rejected() -> None:
    raw = command(
        "task.create",
        {"task_type": "prepare", "title": "t", "budget": {"max_wall_ms": 10**12, "max_attempts": 1}},
        project_id=PROJECT,
    )
    _rejects(raw, "max_wall_ms")


def test_non_object_command_is_rejected() -> None:
    with pytest.raises(ForgeError, match="must be a JSON object"):
        parse_command(["not", "an", "object"])


# ----------------------------------------------------------------- schemas --


def test_committed_schemas_match_the_models() -> None:
    generated = schemas.generate_schemas()
    for name, schema in generated.items():
        assert schemas.load_schema(name) == schema, f"{name}.json is stale; run python -m forge.contracts.schemas --write"


def test_schemas_load_with_networking_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema loading attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for name in schemas.SCHEMA_MODELS:
        assert schemas.load_schema(name)["$id"] == f"forge:contracts/{name}"


def test_exported_schema_works_for_a_non_python_client() -> None:
    envelope = schemas.load_schema("command_envelope")
    params = schemas.load_schema("params.task.create")
    good = command(
        "task.create", {"task_type": "prepare", "title": "t", "budget": BUDGET}, project_id=PROJECT
    )
    jsonschema.validate(good, envelope)
    jsonschema.validate(good["params"], params)

    bad = dict(good, project_id="project_not-a-uuid")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, envelope)


def test_unknown_schema_name_is_rejected() -> None:
    with pytest.raises(ForgeError, match="unknown schema"):
        schemas.load_schema("../../etc/passwd")
