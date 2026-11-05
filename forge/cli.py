"""Development command-line tool (in-process).

This is NOT the final FORGE client. Ticket F08 replaces it with a client that
talks to the controller over authenticated JSON-RPC. Until then, these
commands let you drive and inspect the record layer directly:

    forge-dev demo    --root DIR   run the Session 1 walkthrough
    forge-dev inspect --root DIR   print events and projection rows
    forge-dev verify  --root DIR   replay events and compare with live tables
    forge-dev summary --root DIR   standup built from records (no model)
          [--since N] [--as-of N] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from forge.authority.grants import TrustStore
from forge.contracts.clock import SystemClock
from forge.contracts.commands import ActorContext
from forge.contracts.errors import ForgeError
from forge.contracts.ids import new_id
from forge.controller.service import Controller
from forge.record.projections import dump_projections
from forge.record.store import ProjectStore
from forge.reports.standup import render_standup
from forge.reports.summary import build_summary


def _paths(root: Path) -> tuple[Path, Path]:
    return root / "project" / "state.db", root / "user" / "trust.db"


def _say(text: str) -> None:
    print(text, flush=True)


def _cmd(method: str, params: dict[str, Any], project_id: str | None = None, **extra: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "schema_version": "1.0",
        "method": method,
        "idempotency_key": extra.pop("key", new_id("request")),
        "params": params,
    }
    if project_id:
        raw["project_id"] = project_id
    raw.update(extra)
    return raw


def demo(root: Path) -> int:
    state_path, trust_path = _paths(root)
    if state_path.exists():
        _say(f"{state_path} already exists; choose an empty --root")
        return 2
    me = ActorContext(actor_id=new_id("actor"), kind="human", channel="trusted_local")
    with ProjectStore.open(state_path) as store, TrustStore.open(trust_path) as trust:
        controller = Controller(store, trust_store=trust)

        _say("1. Create a project (no model involved).")
        receipt = controller.apply_command(
            _cmd("project.create", {"name": "Packet reader", "objective": "Read packets; reject incomplete ones"}),
            me,
        )
        project_id = receipt.project_id
        _say(f"   project_id = {project_id}   event_seqs = {receipt.event_seqs}")

        _say("2. Create a criterion: what evidence is needed for one claim.")
        receipt = controller.apply_command(
            _cmd(
                "criterion.create",
                {"subject": "A truncated payload yields a structured error and nonzero exit",
                 "assessment_method": "test_suite"},
                project_id,
            ),
            me,
        )
        criterion_id = receipt.result["criterion_id"]
        _say(f"   criterion_id = {criterion_id}")

        _say("3. Create a task that cites the criterion at revision 1.")
        params = {
            "task_type": "prepare",
            "title": "Define the packet format and boundary cases",
            "criterion_refs": [{"id": criterion_id, "revision": 1}],
            "budget": {"max_wall_ms": 600_000, "max_attempts": 3},
        }
        receipt = controller.apply_command(_cmd("task.create", params, project_id, key="demo-task"), me)
        task_id = receipt.result["task_id"]
        _say(f"   task_id = {task_id}   status = {store.task(task_id).status}")

        _say("4. Deliver the SAME command again (a network retry).")
        again = controller.apply_command(_cmd("task.create", params, project_id, key="demo-task"), me)
        _say(f"   same command_id: {again.command_id == receipt.command_id}   "
             f"duplicate_delivery = {again.duplicate_delivery}   tasks in table = "
             f"{len(store.tasks())}")

        _say("5. Two clients both read the task at record_version 1.")
        controller.apply_command(
            _cmd("task.transition",
                 {"task_id": task_id, "to_status": "blocked",
                  "reason": "waiting for the vendor's packet format document"},
                 project_id, expected_record_version=1),
            me,
        )
        _say("   Client A moved it to 'blocked' (now version 2).")
        try:
            controller.apply_command(
                _cmd("task.transition",
                     {"task_id": task_id, "to_status": "cancelled", "reason": "no longer needed"},
                     project_id, expected_record_version=1),
                me,
            )
        except ForgeError as error:
            _say(f"   Client B was refused: {error.code.value}: {error.message}")
        _say(f"   Task status is still {store.task(task_id).status!r}.")

        _say("6. Replay the event log and compare with the live tables.")
        differences = store.verify_projections()
        _say(f"   differences: {differences or 'none'}")

        _say("7. Standup built from the records alone (no model):\n")
        _say(render_standup(build_summary(store, now=SystemClock().now())))

    _say(f"Inspect it yourself:  forge-dev inspect --root {root}")
    _say(f"Standup any time:     forge-dev summary --root {root}")
    return 0


def inspect(root: Path) -> int:
    state_path, _ = _paths(root)
    if not state_path.exists():
        _say(f"no project database at {state_path}")
        return 2
    with ProjectStore.open(state_path) as store:
        _say("EVENTS (authoritative, append-only)")
        after = 0
        while batch := store.events(after_seq=after, limit=200):
            for event in batch:
                _say(f"  #{event.event_seq} {event.event_type} by {event.actor_id[:14]}... "
                     f"command {event.command_id[:16]}...")
                _say(f"      {json.dumps(event.payload, sort_keys=True)[:160]}")
            after = batch[-1].event_seq
        _say("\nPROJECTIONS (derived from events; rebuildable)")
        for table, rows in dump_projections(store.conn).items():
            _say(f"  {table}: {len(rows)} row(s)")
            for row in rows:
                _say(f"      {json.dumps(row, sort_keys=True)[:160]}")
    return 0


def verify(root: Path) -> int:
    state_path, _ = _paths(root)
    with ProjectStore.open(state_path) as store:
        store.integrity_check()
        differences = store.verify_projections()
    _say("projections match the event log" if not differences else "\n".join(differences))
    return 0 if not differences else 1


def summary(root: Path, since: int, as_of: int | None, as_json: bool) -> int:
    state_path, _ = _paths(root)
    if not state_path.exists():
        _say(f"no project database at {state_path}")
        return 2
    with ProjectStore.open(state_path) as store:
        report = build_summary(store, now=SystemClock().now(), since_seq=since, as_of_seq=as_of)
    _say(report.model_dump_json(indent=2) if as_json else render_standup(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-dev", description="FORGE development tool")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("demo", "inspect", "verify", "summary"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=Path("forge-data"))
        if name == "summary":
            command.add_argument("--since", type=int, default=0, help="report changes after this event")
            command.add_argument("--as-of", type=int, default=None, help="show the project at this event")
            command.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    try:
        if args.action == "summary":
            return summary(args.root, args.since, args.as_of, args.json)
        return {"demo": demo, "inspect": inspect, "verify": verify}[args.action](args.root)
    except ForgeError as error:
        _say(f"error: {error.code.value}: {error.message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
