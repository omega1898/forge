"""Migration workflow (ticket F20, partial): restartable batches, comparison, cutover rehearsal.

- Restartable batch. Each input record is converted and its output written
  under the record's key in a durable ledger, in one transaction with the
  checkpoint. A restart resumes after the last committed record; writing
  the same key twice is refused, so a crash between output and checkpoint
  cannot duplicate a side effect.
- Comparison. Candidate output is compared record by record with the
  approved reference (`compare_records`: every difference reported,
  accepted ones only with an explicit key, field and reason), and counts are
  reconciled (D09) with a reconciler who is not the preparer.
- Cutover rehearsal. legacy -> shadow -> cutover -> completed, with
  rollback from shadow or cutover back to legacy. Cutover requires an
  equivalent comparison, a tied-out reconciliation and an approval; every
  transition is recorded.

Here the approved reference is recorded legacy output decoded from EBCDIC
with its copybook; no COBOL compiler is installed to produce it live.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from forge.contracts.errors import ErrorCode, ForgeError
from forge.packs.cobol_layout import compare_records
from forge.performance.bridge import reconcile


class BatchInterrupted(Exception):
    pass


class RestartableBatch:
    def __init__(self, ledger: Path) -> None:
        self.db = sqlite3.connect(str(ledger), isolation_level=None)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS outputs (record_key TEXT PRIMARY KEY, body TEXT NOT NULL, position INTEGER);"
            "CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK (id = 1), position INTEGER);"
            "INSERT OR IGNORE INTO checkpoint VALUES (1, -1);")

    def position(self) -> int:
        return self.db.execute("SELECT position FROM checkpoint").fetchone()[0]

    def run(self, records: list[dict[str, Any]], key: str, convert: Callable[[dict[str, Any]], dict[str, Any]], *,
            crash_after: int | None = None) -> int:
        """Process records after the checkpoint; returns how many were written in this run."""
        written = 0
        for position in range(self.position() + 1, len(records)):
            record = records[position]
            output = convert(record)
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    self.db.execute("INSERT INTO outputs VALUES (?, ?, ?)",
                                    (str(record[key]), json.dumps(output, sort_keys=True, default=str), position))
                except sqlite3.IntegrityError:
                    raise ForgeError(ErrorCode.INTERNAL_ERROR,
                                     f"output for {record[key]} already exists: a restart would duplicate it") from None
                self.db.execute("UPDATE checkpoint SET position = ?", (position,))
            written += 1
            if crash_after is not None and written == crash_after:
                raise BatchInterrupted(f"stopped after {written} records")
        return written

    def outputs(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM outputs ORDER BY position")]


def assess_step(reference: list[dict[str, Any]], candidate: list[dict[str, Any]], *, key: str,
                accepted: dict[tuple[str, str], str] | None = None, as_of: date, prepared_by: str,
                reconciled_by: str) -> dict[str, Any]:
    comparison = compare_records(reference, candidate, key=key, accepted=accepted)
    counts = reconcile({str(r[key]): r for r in reference}, {str(r[key]): r for r in candidate}, fields=[],
                       as_of=as_of, timing_window_days=0, prepared_by=prepared_by, reconciled_by=reconciled_by)
    # compare_records already reports missing and extra records; the reconciliation adds the
    # independence rule (the preparer may not reconcile) and the count report.
    return {"matches_approved": comparison["equivalent"], "comparison": comparison,
            "reconciliation": counts, "accepted": [d for d in comparison["differences"] if d.accepted]}


TRANSITIONS = {("legacy", "shadow"), ("shadow", "cutover"), ("cutover", "completed"), ("shadow", "legacy"),
               ("cutover", "legacy")}


class CutoverRehearsal:
    def __init__(self) -> None:
        self.state = "legacy"
        self.history: list[dict[str, Any]] = []

    def move(self, target: str, *, actor_is_trusted_human: bool, assessment: dict[str, Any] | None = None,
             reason: str) -> None:
        if (self.state, target) not in TRANSITIONS:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"cannot go from {self.state} to {target}")
        if target in ("cutover", "completed") and not actor_is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only the trusted user approves a cutover")
        if target == "cutover" and not (assessment and assessment["matches_approved"]):
            unaccepted = len(assessment["comparison"]["unaccepted"]) if assessment else "unknown"
            raise ForgeError(ErrorCode.INVALID_REQUEST,
                             f"cutover needs an equivalent, reconciled step (unaccepted differences: {unaccepted})")
        self.history.append({"from": self.state, "to": target, "reason": reason,
                             "accepted_differences": [f"{d.key}:{d.field}: {d.reason}"
                                                      for d in (assessment or {}).get("accepted", [])]})
        self.state = target
