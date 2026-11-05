"""`forge`: the command-line client for a running FORGE controller.

The CLI holds no business logic. Every command is one call to the local
API, the same call the editor extension will make, so a CLI command and an
editor action produce the same recorded state transition.

    forge serve   --root DIR                start the controller and API (foreground)
    forge init    --root DIR --name N --objective O [--source DIR]
    forge open    --root DIR
    forge resume  --root DIR [--since N]
    forge intent  --root DIR "what you want built" [--mode build|pair|learn|review]
    forge capture --root DIR [--dirty PATH ...]
    forge tasks   --root DIR [--status S]
    forge task    --root DIR TASK_ID
    forge cancel  --root DIR TASK_ID --reason TEXT
    forge evidence --root DIR TASK_ID
    forge summary --root DIR
    forge events  --root DIR [--after N] [--follow]

Mutating commands accept --key to retry safely: the same key and content
returns the original result instead of acting twice.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from forge.api.client import ForgeClient, RpcError
from forge.api.launcher import launch, read_connection
from forge.contracts.errors import ForgeError
from forge.reports.standup import escape, render_standup
from forge.reports.summary import ProjectSummary


def _client(root: Path) -> ForgeClient:
    info = read_connection(root)
    return ForgeClient(info["url"], info["token"])


def _project_id(client: ForgeClient) -> str:
    return client.call("project.open")["project"]["project_id"]


def _print(data: Any, as_json: bool, text: str) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False) if as_json else text)


def cmd_serve(args: argparse.Namespace) -> int:
    launched = launch(args.root, port=args.port)
    print(f"FORGE controller listening on {launched.server.url} (connection file written for clients)")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    launched.close()
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    client = _client(args.root)
    params: dict[str, Any] = {"name": args.name, "objective": args.objective,
                              "source_roots": ["main"] if args.source else []}
    receipt = client.call("project.create", {"idempotency_key": args.key or f"init-{args.name}"[:120],
                                             "params": params})
    project_id = receipt["project_id"]
    if args.source:
        client.call("authority.approve_source_root", {"label": "main", "path": str(Path(args.source).resolve())})
    _print(receipt, args.json, f"project {project_id} created")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    data = _client(args.root).call("project.open")
    project = data["project"]
    _print(data, args.json, f"{escape(project['name'])} ({project['project_id']})\n"
                            f"objective: {escape(project['objective'], 400)}\n"
                            f"last event: #{project['last_event_seq']}, plan revision {project['plan_revision']}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    data = _client(args.root).call("project.resume", {"last_seen_event_seq": args.since})
    text = f"{data['missed_events']} event(s) since #{args.since}. ({data['labels']})\n\n"
    text += render_standup(ProjectSummary.model_validate(data["summary"]))
    _print(data, args.json, text)
    return 0


def cmd_intent(args: argparse.Namespace) -> int:
    client = _client(args.root)
    receipt = client.mutate("intent.submit", _project_id(client), {"text": args.text, "mode": args.mode}, key=args.key)
    result = receipt["result"]
    _print(receipt, args.json, f"intent {result['intent_id']} recorded; planning task {result['planning_task_id']}"
                               + (" (repeat delivery: original result returned)" if receipt["duplicate_delivery"] else ""))
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    client = _client(args.root)
    receipt = client.mutate("workspace.capture", _project_id(client),
                            {"source_root": args.source_root, "dirty_paths": args.dirty}, key=args.key)
    result = receipt["result"]
    _print(receipt, args.json, f"snapshot {result['snapshot_id']}: {result['file_count']} files, "
                               f"{result['excluded_count']} excluded, identity {result['manifest_digest']}")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    data = _client(args.root).call("task.list", {"status": args.status} if args.status else {})
    lines = [f"{task['status']:<17} {task['task_type']:<8} {task['task_id']}  {escape(task['title'], 80)}"
             for task in data["tasks"]]
    _print(data, args.json, "\n".join(lines) or "no tasks")
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    data = _client(args.root).call("task.get", {"task_id": args.task_id})
    task = data["task"]
    text = (f"{escape(task['title'])}\n{task['task_id']}  {task['task_type']}  {task['status']}  "
            f"version {task['record_version']}\njobs: {len(data['jobs'])}")
    _print(data, args.json, text)
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    client = _client(args.root)
    receipt = client.mutate("task.cancel", _project_id(client), {"task_id": args.task_id, "reason": args.reason},
                            key=args.key)
    _print(receipt, args.json, f"{args.task_id}: {receipt['result']['status']}")
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    data = _client(args.root).call("evidence.list", {"task_id": args.task_id})
    lines = []
    for item in data["items"]:
        assessment = item["assessment"]
        lines.append(f"{assessment['assessment_result']:<13} {assessment['criterion_ref']['id']} "
                     f"rev {assessment['criterion_ref']['revision']}  snapshot {assessment['snapshot_id']}")
        lines.extend(f"    limitation: {escape(text, 200)}" for text in assessment["limitations"])
    _print(data, args.json, "\n".join(lines) or "no assessments recorded for this task's criteria")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    data = _client(args.root).call("project.summary")
    _print(data, args.json, render_standup(ProjectSummary.model_validate(data)))
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    client = _client(args.root)
    if not args.follow:
        data = client.call("events.list", {"after_seq": args.after})
        _print(data, args.json, "\n".join(f"#{e['event_seq']} {e['event_type']}" for e in data["events"]))
        return 0
    for event in client.events(after=args.after):
        if event.get("gap") or event.get("reload"):
            print("event stream interrupted; re-read with: forge events --after N", file=sys.stderr)
            return 1
        print(json.dumps(event) if args.json else f"#{event['event_seq']} {event['event_type']}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge", description="FORGE command-line client")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, *, mutating: bool = False):  # noqa: ANN001, ANN202
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=Path("forge-data"))
        command.add_argument("--json", action="store_true", help="machine-readable output")
        if mutating:
            command.add_argument("--key", default=None, help="idempotency key for safe retries")
        command.set_defaults(handler=handler)
        return command

    add("serve", cmd_serve).add_argument("--port", type=int, default=0)
    init = add("init", cmd_init, mutating=True)
    init.add_argument("--name", required=True)
    init.add_argument("--objective", required=True)
    init.add_argument("--source", default=None, help="project folder to approve as source root 'main'")
    add("open", cmd_open)
    add("resume", cmd_resume).add_argument("--since", type=int, default=0)
    intent = add("intent", cmd_intent, mutating=True)
    intent.add_argument("text")
    intent.add_argument("--mode", default="build", choices=["build", "pair", "learn", "review"])
    capture = add("capture", cmd_capture, mutating=True)
    capture.add_argument("--source-root", default="main")
    capture.add_argument("--dirty", action="append", default=[], help="path with unsaved editor changes")
    add("tasks", cmd_tasks).add_argument("--status", default=None)
    add("task", cmd_task).add_argument("task_id")
    cancel = add("cancel", cmd_cancel, mutating=True)
    cancel.add_argument("task_id")
    cancel.add_argument("--reason", required=True)
    add("evidence", cmd_evidence).add_argument("task_id")
    add("summary", cmd_summary)
    events = add("events", cmd_events)
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--follow", action="store_true")

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except RpcError as error:
        code = error.forge_code or error.code
        print(f"error: {code}: {error.message}", file=sys.stderr)
        return 1
    except ForgeError as error:
        print(f"error: {error.code.value}: {error.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
