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

    forge check [tests|coverage|analyze|sanitize] [--accept-tests] [--no-capture] [--snapshot ID]
    forge check init [--source DIR]               write a starter forge.toml for a project folder
    forge check standards                         list the coding standards forge.toml can choose

    forge comply domains                          list compliance domains and standards
    forge comply set DOMAIN [--level A] [--baseline high] [--option K=V] [--catalog ID]
    forge comply check  [--snapshot ID] [--requirements PATH] [--output FILE]
    forge comply report [--snapshot ID] [--domain D] [--output FILE.md|FILE.json]
    forge comply attest CATALOG LABEL... --statement TEXT [--scope process|snapshot]
    forge comply attest --file ATTESTATIONS.csv   (columns: catalog,label,statement)
    forge comply import-catalog FILE --id ID --title T [--format oscal|csv] [--mapping CSV]
    forge comply import-vulns FILE_OR_DIR         (OSV JSON records)

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
from forge.contracts.errors import ErrorCode, ForgeError
from forge.reports.standup import escape, render_standup
from forge.reports.summary import ProjectSummary


def _client(root: Path, *, timeout: float = 60.0) -> ForgeClient:
    info = read_connection(root)
    return ForgeClient(info["url"], info["token"], timeout=timeout)


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


# -------------------------------------------------------------------- check

CHECK_TIMEOUT_SECONDS = 3600.0


def _check_line(step: dict[str, Any]) -> list[str]:
    name, result = step["step"], step.get("result", "?")
    lines: list[str] = []
    if name in ("tests", "sanitize") and step.get("cases") is not None:
        cases = step["cases"]
        passed = sum(1 for c in cases if c["outcome"] == "pass")
        head = f"{name:<9} {result:<13} {passed}/{len(cases)} cases passed ({step.get('toolchain', '?')})"
        lines.append(head)
        lines.extend(f"    {c['case_id']}: {c['outcome']} {_plain('; '.join(c['reasons']), 160)}"
                     for c in cases if c["outcome"] != "pass")
        lines.extend(f"    sanitizer: {_plain(r, 200)}" for r in step.get("sanitizer_reports", []))
    elif name == "coverage" and "mcdc_percent" in step:
        lines.append(f"{name:<9} {result:<13} statement {step['statement_percent']:.1f}%  decision "
                     f"{step['decision_percent']:.1f}%  MC/DC {step['mcdc_percent']:.1f}%  "
                     f"({step['cases_run']} cases)")
        if step.get("uncovered_conditions"):
            shown = ", ".join(f"{c['path']}:{c['line']}" + (" (true)" if c["not_covered_true"] else "")
                              + (" (false)" if c["not_covered_false"] else "") for c in step["uncovered_conditions"][:8])
            lines.append(f"    uncovered conditions: {_plain(shown, 400)}")
        if step.get("excluded_generated"):
            lines.append(f"    not counted (generated by the compiler): {', '.join(step['excluded_generated'][:5])}")
        lines.extend(f"    integrity: {_plain(p, 200)}" for p in step.get("integrity_problems", []))
    elif name == "analyze" and "finding_count" in step:
        lines.append(f"{name:<9} {result:<13} {step['finding_count']} finding(s), {step.get('blocking_count', 0)} "
                     f"blocking; standards: {', '.join(step.get('standards', [])) or '-'}")
        lines.extend(f"    {_plain(f, 240)}" for f in step.get("findings", [])[:10])
        if step.get("missing_tools"):
            lines.append(f"    not installed: {', '.join(step['missing_tools'])} (the standards above need them)")
        lines.extend(f"    needs your attestation: {_plain(a, 160)}" for a in step.get("attestations", []))
    else:
        lines.append(f"{name:<9} {result:<13} {_plain(step.get('reason') or '', 300)}")
    lines.extend(f"    note: {_plain(n, 300)}" for n in step.get("notices", []))
    return lines


def cmd_check(args: argparse.Namespace) -> int:
    if args.step == "init":
        return _check_init(args)
    if args.step == "standards":
        from forge.quality.registry import DEFAULTS, PROFILES

        lines = [f"{p.profile_id:<20} {p.title}" + (f"\n{'':<20} source: {p.source}" if p.source else "")
                 for p in PROFILES.values()]
        lines.append("\ndefaults by toolchain: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in DEFAULTS.items()))
        lines.append('choose in forge.toml:  [quality]  standards = ["cert-c", "power-of-ten"]')
        print("\n".join(lines))
        return 0
    client = _client(args.root, timeout=CHECK_TIMEOUT_SECONDS)
    snapshot_id = args.snapshot
    lines = []
    if snapshot_id is None and not args.no_capture:
        captured = client.mutate("workspace.capture", _project_id(client), {"source_root": args.source_root})
        snapshot_id = captured["result"]["snapshot_id"]
        lines.append(f"captured {snapshot_id} ({captured['result']['file_count']} files)")
    data = client.call("check.run", {"step": args.step, "snapshot_id": snapshot_id,
                                     "accept_tests": args.accept_tests})
    if args.json:
        _print(data, True, "")
        return 0
    lines.append(f"snapshot {data['snapshot_id']}")
    for step in data["steps"]:
        lines.extend(_check_line(step))
    compliance = data.get("compliance")
    if compliance:
        lines.append(f"compliance gate {compliance['gate_status'].upper()} ({compliance['profile']})")
        lines.extend(f"    {name}: {item['status']}: {_plain(item['detail'], 160)}"
                     for name, item in compliance["checks"].items() if item["status"] != "met")
        if compliance["next"]:
            lines.append("next:")
            lines.extend(f"  {n}. {_plain(step, 300)}" for n, step in enumerate(compliance["next"], 1))
    print("\n".join(lines))
    failed = any(s.get("result") in ("fail", "error") for s in data["steps"])
    return 1 if failed else 0


def _check_init(args: argparse.Namespace) -> int:
    from forge.check.project import PROJECT_FILE, detect, starter

    folder = Path(args.source).resolve()
    target = folder / PROJECT_FILE
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to replace it)", file=sys.stderr)
        return 1
    toolchain, sources = detect(folder)
    target.write_text(starter(toolchain, sources), encoding="utf-8")
    print(f"wrote {target}: toolchain {toolchain}, sources {', '.join(sources)}\n"
          "edit the example test, then run: forge capture && forge check")
    return 0


# --------------------------------------------------------------- compliance

_SHORT_OPTIONS = ("level", "baseline", "safety_class", "asil", "sil", "software_class", "coverage")


def _comply_options(args: argparse.Namespace) -> dict[str, str] | None:
    options = {name: value for name in _SHORT_OPTIONS if (value := getattr(args, name, None)) is not None}
    for item in getattr(args, "option", None) or []:
        name, separator, value = item.partition("=")
        if not separator:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"--option needs NAME=VALUE, got {item!r}")
        options[name.strip()] = value.strip()
    return options or None


def _plain(text: str, limit: int) -> str:
    """Terminal-safe: no control characters (a hostile file name cannot move the cursor or recolor output)."""
    flat = "".join(ch if ch.isprintable() else " " for ch in str(text))
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _write_report(data: dict[str, Any], output: Path | None) -> str:
    if output is None:
        return data["markdown"]
    text = json.dumps(data["report"], indent=2) if output.suffix == ".json" else data["markdown"]
    output.write_text(text, encoding="utf-8")
    return f"report written to {output}"


def cmd_comply_domains(args: argparse.Namespace) -> int:
    data = _client(args.root).call("compliance.domains")
    lines = []
    for domain in data["domains"]:
        options = "; ".join(f"--{k.replace('_', '-')} {'|'.join(v['values'])} (default {v['default']})"
                            for k, v in domain["options"].items())
        lines.append(f"{domain['profile_id']:<11} {domain['title']}" + (f"\n{'':<11} {options}" if options else ""))
    active = data.get("active")
    lines.append("\nactive: " + (active["subject"] if active else "none (forge comply set DOMAIN)"))
    _print(data, args.json, "\n".join(lines))
    return 0


def cmd_comply_set(args: argparse.Namespace) -> int:
    data = _client(args.root).call("compliance.set_domain", {"domain": args.domain, "options": _comply_options(args),
                                                             "catalogs": args.catalog})
    profile = data["profile"]
    text = (f"compliance domain set: {profile['title']}\n"
            f"gate checks: {', '.join(profile['gate_checks']) or 'none'}\n"
            + "".join(f"note: {note}\n" for note in profile["notes"])
            + "new execute tasks now carry this gate; run `forge check` to see where you stand")
    _print(data, args.json, text)
    return 0


def cmd_comply_check(args: argparse.Namespace) -> int:
    data = _client(args.root).call("compliance.check", {"snapshot_id": args.snapshot,
                                                        "requirements": args.requirements})
    report = data["report"]
    if args.json:
        _print(data, True, "")
        return 0
    lines = [f"snapshot {data['snapshot_id']}: gate {report['gate_status'].upper()} ({report['profile']['title']})"]
    for check_id in report["profile"]["gate_checks"]:
        check = report["checks"][check_id]
        lines.append(f"  {check['status']:<15} {check['title']}: {_plain(check['detail'], 160)}")
    for section in report["catalogs"]:
        counts = section["counts"]
        lines.append(f"{section['catalog_id']} ({section['selection'].split(' (')[0]}): met {counts['met']}, gap "
                     f"{counts['gap']}, incomplete {counts['incomplete']}, attested {counts['attested']}, needs "
                     f"attestation {counts['needs_attestation']}")
    if data["next"]:
        lines.append("next:")
        lines.extend(f"  {n}. {_plain(step, 300)}" for n, step in enumerate(data["next"], 1))
    if args.output:
        lines.append(_write_report(data, args.output))
    print("\n".join(lines))
    return 0


def cmd_comply_report(args: argparse.Namespace) -> int:
    data = _client(args.root).call("compliance.report", {"snapshot_id": args.snapshot, "domain": args.domain,
                                                         "options": _comply_options(args)})
    if args.json and args.output is None:
        _print(data["report"], True, "")
        return 0
    print(_write_report(data, args.output))
    return 0


def cmd_comply_attest(args: argparse.Namespace) -> int:
    import csv

    client = _client(args.root)
    batches: dict[tuple[str, str], list[str]] = {}
    if args.file:
        with open(args.file, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row.get("catalog", "").strip(), row.get("statement", "").strip())
                batches.setdefault(key, []).append(row.get("label", "").strip())
    else:
        if not args.catalog or not args.labels or not (args.statement or args.withdraw):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "give CATALOG LABEL... --statement TEXT, or --file CSV")
        batches[(args.catalog, args.statement or "")] = args.labels
    recorded = []
    for (catalog, statement), labels in batches.items():
        result = client.call("compliance.attest", {"snapshot_id": args.snapshot, "catalog": catalog, "labels": labels,
                                                   "statement": statement, "scope": args.scope,
                                                   "withdraw": args.withdraw})
        recorded.extend(result["evidence_ids"])
    _print({"evidence_ids": recorded}, args.json, f"{len(recorded)} attestation(s) recorded in your name")
    return 0


def cmd_comply_import_catalog(args: argparse.Namespace) -> int:
    from forge.compliance.oscal import import_catalog
    from forge.compliance.service import parse_csv_catalog, parse_mapping_csv

    raw = Path(args.file).read_bytes()
    kind = args.format or ("csv" if str(args.file).lower().endswith(".csv") else "oscal")
    source = args.source or f"file:{Path(args.file).name}"
    if kind == "csv":
        catalog = parse_csv_catalog(raw, catalog_id=args.id, title=args.title, version=args.version or "",
                                    source=source, license_note=args.license)
    else:
        catalog = import_catalog(raw, catalog_id=args.id, source_url=source,
                                 license_note=args.license).model_dump(mode="json")
        if args.title:
            catalog["title"] = args.title
    mappings = parse_mapping_csv(Path(args.mapping).read_bytes()) if args.mapping else {}
    data = _client(args.root).call("compliance.import_catalog", {"catalog": catalog, "mappings": mappings})
    _print(data, args.json, f"catalog {data['catalog_id']} imported: {data['controls']} controls, {data['mapped']} "
                            "mapped to FORGE checks (stored as sensitive; never sent to a model)")
    return 0


def _osv_records(path: Path) -> list[dict[str, Any]]:
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    records: list[dict[str, Any]] = []
    for item in files:
        data = json.loads(item.read_text(encoding="utf-8"))
        records.extend(data if isinstance(data, list) else data.get("vulns", [data]) if isinstance(data, dict) else [])
    return records


def cmd_comply_import_vulns(args: argparse.Namespace) -> int:
    client = _client(args.root)
    records = _osv_records(Path(args.path))
    imported, batch, size = 0, [], 0
    for record in records + [None]:            # None flushes the last batch
        encoded = 0 if record is None else len(json.dumps(record))
        if batch and (record is None or size + encoded > 700_000):
            imported += client.call("compliance.import_vulnerabilities",
                                    {"records": batch, "source": str(args.path)})["records"]
            batch, size = [], 0
        if record is not None:
            batch.append(record)
            size += encoded
    _print({"records": imported}, args.json, f"{imported} vulnerability records imported (offline database)")
    return 0


def _add_comply(sub, common: argparse.ArgumentParser) -> None:  # noqa: ANN001
    comply = sub.add_parser("comply", help="compliance domain, checks, reports and attestations")
    actions = comply.add_subparsers(dest="comply_action", required=True)

    def action(name: str, handler, help_text: str):  # noqa: ANN001, ANN202
        parser = actions.add_parser(name, parents=[common], help=help_text)
        parser.set_defaults(handler=handler)
        return parser

    def option_flags(parser: argparse.ArgumentParser) -> None:
        for name in _SHORT_OPTIONS:
            parser.add_argument(f"--{name.replace('_', '-')}", dest=name, default=None)
        parser.add_argument("--option", action="append", default=[], help="any domain option as NAME=VALUE")

    action("domains", cmd_comply_domains, "list domains, their options and standards")
    chosen = action("set", cmd_comply_set, "choose what the software is built for")
    chosen.add_argument("domain")
    option_flags(chosen)
    chosen.add_argument("--catalog", action="append", default=[], help="also report against an imported catalog")
    check = action("check", cmd_comply_check, "produce evidence, assess the gate, summarize")
    check.add_argument("--snapshot", default=None, help="snapshot id (default: the latest)")
    check.add_argument("--requirements", default=None, help="requirements list inside the snapshot (CSV)")
    check.add_argument("--output", type=Path, default=None, help="also write the full report (.md or .json)")
    report = action("report", cmd_comply_report, "write the control-by-control report")
    report.add_argument("--snapshot", default=None)
    report.add_argument("--domain", default=None, help="report against another domain without changing the gate")
    option_flags(report)
    report.add_argument("--output", type=Path, default=None)
    attest = action("attest", cmd_comply_attest, "record your statement for controls FORGE cannot check")
    attest.add_argument("catalog", nargs="?")
    attest.add_argument("labels", nargs="*")
    attest.add_argument("--statement", default=None)
    attest.add_argument("--file", default=None, help="CSV with columns catalog,label,statement")
    attest.add_argument("--scope", default="process", choices=["process", "snapshot"])
    attest.add_argument("--snapshot", default=None)
    attest.add_argument("--withdraw", action="store_true", help="withdraw an earlier attestation")
    imported = action("import-catalog", cmd_comply_import_catalog, "import a standard you hold (OSCAL or CSV)")
    imported.add_argument("file")
    imported.add_argument("--id", required=True, help="short name, e.g. do-178c")
    imported.add_argument("--title", default="")
    imported.add_argument("--version", default=None)
    imported.add_argument("--format", choices=["oscal", "csv"], default=None)
    imported.add_argument("--mapping", default=None, help="CSV: label,check_id[;check_id]")
    imported.add_argument("--source", default=None, help="where the original came from (shown in reports)")
    imported.add_argument("--license", default="licensed copy held by the user; not redistributable")
    vulns = action("import-vulns", cmd_comply_import_vulns, "import OSV vulnerability records for offline scans")
    vulns.add_argument("path")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge", description="FORGE command-line client")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=Path("forge-data"))
    common.add_argument("--json", action="store_true", help="machine-readable output")

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
    _add_comply(sub, common)
    check = sub.add_parser("check", parents=[common], help="build, test, measure coverage and analyze from forge.toml")
    check.add_argument("step", nargs="?", default="all",
                       choices=["all", "tests", "coverage", "analyze", "sanitize", "init", "standards"])
    check.add_argument("--snapshot", default=None, help="check this snapshot instead of capturing a new one")
    check.add_argument("--no-capture", action="store_true", help="check the latest snapshot without capturing")
    check.add_argument("--source-root", default="main")
    check.add_argument("--accept-tests", action="store_true",
                       help="accept the tests in forge.toml as the protected suite (after reviewing them)")
    check.add_argument("--source", default=".", help="init: the project folder")
    check.add_argument("--force", action="store_true", help="init: replace an existing forge.toml")
    check.set_defaults(handler=cmd_check)

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
