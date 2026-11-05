"""Ticket F20 (partial): migration step, restart, cutover and rollback rehearsal.

Acceptance: a bounded step matches approved behavior; accepted differences
remain explicit. The approved behavior is recorded legacy output (EBCDIC
with a copybook); COBOL itself cannot run on this host.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from forge.contracts.errors import ForgeError
from forge.packs.cobol_layout import cobol_round, decode_file, parse_copybook
from forge.workflows.migration import BatchInterrupted, CutoverRehearsal, RestartableBatch, assess_step
from tests.test_jvm_cobol import COPYBOOK, customer

AS_OF = date(2026, 11, 4)
SOURCE = [customer(f"C{n:05d}", f"CUSTOMER {n}", f"{n * 101}.05", "05250", 0) for n in range(1, 9)]


def legacy_output() -> list[dict]:
    """What the COBOL job produced (recorded): interest with COBOL ROUNDED."""
    rows = decode_file(b"".join(SOURCE), parse_copybook(COPYBOOK))
    return [{"CUST-ID": r["CUST-ID"], "INTEREST": str(cobol_round(r["BALANCE"] * r["RATE"], 2))} for r in rows]


def migrated(record: dict) -> dict:
    """The new implementation's conversion of one record."""
    return {"CUST-ID": record["CUST-ID"], "INTEREST": str(cobol_round(record["BALANCE"] * record["RATE"], 2))}


def inputs() -> list[dict]:
    return decode_file(b"".join(SOURCE), parse_copybook(COPYBOOK))


def assess(candidate: list[dict], accepted=None) -> dict:  # noqa: ANN001
    return assess_step(legacy_output(), candidate, key="CUST-ID", accepted=accepted, as_of=AS_OF,
                       prepared_by="actor_migration", reconciled_by="actor_reviewer")


def test_a_restarted_batch_matches_the_approved_output_without_duplicates(tmp_path) -> None:  # noqa: ANN001
    batch = RestartableBatch(tmp_path / "batch.db")
    with pytest.raises(BatchInterrupted):
        batch.run(inputs(), "CUST-ID", migrated, crash_after=3)
    resumed = RestartableBatch(tmp_path / "batch.db")          # a new process after the crash
    assert resumed.position() == 2 and resumed.run(inputs(), "CUST-ID", migrated) == 5
    result = assess(resumed.outputs())
    assert result["matches_approved"] and result["accepted"] == []


def test_a_lost_checkpoint_cannot_duplicate_a_side_effect(tmp_path) -> None:  # noqa: ANN001
    batch = RestartableBatch(tmp_path / "batch.db")
    batch.run(inputs(), "CUST-ID", migrated, crash_after=None)
    batch.db.execute("UPDATE checkpoint SET position = 1")        # seeded defect: checkpoint rolled back
    with pytest.raises(ForgeError, match="would duplicate"):
        batch.run(inputs(), "CUST-ID", migrated)


def test_differences_block_cutover_unless_explicitly_accepted_and_stay_visible() -> None:
    candidate = [dict(row) for row in legacy_output()]
    candidate[2]["INTEREST"] = str(Decimal(candidate[2]["INTEREST"]) + Decimal("0.01"))   # one cent off
    rehearsal = CutoverRehearsal()
    rehearsal.move("shadow", actor_is_trusted_human=True, reason="parallel run")
    with pytest.raises(ForgeError, match="unaccepted differences: 1"):
        rehearsal.move("cutover", actor_is_trusted_human=True, assessment=assess(candidate), reason="go")
    accepted = assess(candidate, {(candidate[2]["CUST-ID"], "INTEREST"): "regulator approved new rounding"})
    with pytest.raises(ForgeError, match="trusted user"):
        rehearsal.move("cutover", actor_is_trusted_human=False, assessment=accepted, reason="go")
    rehearsal.move("cutover", actor_is_trusted_human=True, assessment=accepted, reason="go")
    assert rehearsal.history[-1]["accepted_differences"] == ["C00003:INTEREST: regulator approved new rounding"]
    rehearsal.move("legacy", actor_is_trusted_human=True, reason="rollback rehearsal")
    assert rehearsal.state == "legacy" and [h["to"] for h in rehearsal.history] == ["shadow", "cutover", "legacy"]


def test_a_dropped_record_fails_reconciliation_even_if_every_present_record_matches() -> None:
    result = assess(legacy_output()[:-1])
    assert not result["matches_approved"]
    assert result["comparison"]["missing_records"] == ["C00008"]


def test_the_preparer_cannot_reconcile_their_own_migration() -> None:
    with pytest.raises(ForgeError, match="must not be the actor"):
        assess_step(legacy_output(), legacy_output(), key="CUST-ID", as_of=AS_OF, prepared_by="actor_x",
                    reconciled_by="actor_x")
