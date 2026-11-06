"""`forge formats`: the format layer from the command line, on files on disk, with no project needed.

    forge formats list [--area avionics]   every format FORGE recognizes, and what it does with each
    forge formats show FILE                 what a model would be shown for FILE
    forge formats check FILE...             the syntax gate on files; exit status 1 if any has an error
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge.formats import registry, views
from forge.formats.checks import check
from forge.formats.text import NotText, decode

MAX_FILE = 256 * 1024 * 1024


def _read(path: Path) -> tuple[bytes, int, bool]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(min(size, views.MAX_MEMBER * 4))
    return data, size, len(data) == size


def _capabilities(fmt: registry.FileFormat) -> dict[str, str]:
    if fmt.sensitive:
        return {"read": "never shown (secret store)", "write": "never edited", "check": "-"}
    if fmt.kind == "text":
        read = "text" + (", secrets masked by key" if fmt.secrets else "")
        return {"read": read, "write": "patched, encoding and line endings kept", "check": fmt.check or "-"}
    if fmt.reader == "needs_converter" or fmt.reader is None and fmt.format_id != "safetensors":
        return {"read": "identified; text needs a converter" if fmt.reader else "identified only",
                "write": "not edited", "check": "-"}
    return {"read": f"summarized by the {fmt.reader or fmt.format_id} reader", "write": "not edited", "check": "-"}


def cmd_formats(args: argparse.Namespace) -> int:
    if args.action == "list":
        chosen = registry.formats_for_area(args.area) if args.area else list(registry.FORMATS)
        rows = [{"format": f.format_id, "title": f.title, "suffixes": list(f.suffixes), "names": list(f.names),
                 "areas": list(f.areas), **_capabilities(f)} for f in chosen]
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        if args.area:
            print(f"{registry.AREAS.get(args.area, args.area)}: {len(rows)} formats\n")
        for row in rows:
            where = " ".join(row["suffixes"] + row["names"]) or "(by content)"
            print(f"{row['title']}\n  {where}\n  read: {row['read']}; write: {row['write']}; check: {row['check']}")
        return 0
    status = 0
    results = []
    for name in args.files:
        path = Path(name)
        data, size, complete = _read(path)
        fmt = registry.detect(path.name, data[:8192])
        if args.action == "show":
            result = views.view(path.name, data, size=size, complete=complete, fmt=fmt)
            results.append({"file": name, "format": fmt.format_id, "view": result.text, "notes": result.notes})
            if not args.json:
                print(f"== {name} ({fmt.title})\n{result.render(path.name)}")
            continue
        if fmt.kind != "text" or fmt.sensitive:
            outcome = {"file": name, "format": fmt.format_id, "status": "not_checked",
                       "reason": f"{fmt.title} has no syntax check", "problems": []}
        else:
            try:
                checked = check(fmt, decode(data, fmt), path.name)
            except NotText as error:
                checked = None
                outcome = {"file": name, "format": fmt.format_id, "status": "not_checked", "reason": str(error),
                           "problems": []}
            if checked is not None:
                outcome = {"file": name, "format": fmt.format_id, "status": checked.status, "reason": checked.reason,
                           "problems": [p.render(name) for p in checked.problems]}
                if checked.errors:
                    status = 1
        results.append(outcome)
        if not args.json:
            print(f"{name}: {outcome['status']} ({fmt.title})" + (f" - {outcome['reason']}" if outcome["reason"]
                                                                   else ""))
            for line in outcome["problems"]:
                print(f"  {line}")
    if args.json:
        print(json.dumps(results, indent=2))
    return status


def add_parser(sub) -> None:
    formats = sub.add_parser("formats", help="what FORGE reads, shows and checks for each file type")
    formats.add_argument("action", choices=["list", "show", "check"])
    formats.add_argument("files", nargs="*", help="show/check: files on disk")
    formats.add_argument("--area", choices=sorted(registry.AREAS), help="list: only formats for this work area")
    formats.add_argument("--json", action="store_true", help="machine-readable output")
    formats.set_defaults(handler=cmd_formats)
