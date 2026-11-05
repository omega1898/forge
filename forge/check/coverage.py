"""Structural coverage with GCC 14: statement, branch (for decision coverage) and MC/DC.

How a measurement is made (all inside the sandbox):

1. Coverage job: a small FORGE driver copies the snapshot to scratch, builds
   it with `--coverage -fcondition-coverage`, runs every accepted test case
   against the instrumented program, then copies the counter files (.gcno
   from the compiler, .gcda written by the program) to the job's output.
2. Report job: the gcov matching the compiler reads those counters and
   writes its JSON report. It runs in a fresh sandbox, after the program
   has gone.
3. FORGE summarizes the report for the files in the snapshot only. Files the
   compiler generated (for example GNAT's binder file b~main.adb) are listed
   as excluded, not counted.

Limits that the evidence states:

- The counters are written by the instrumented program itself, so a program
  written to forge them could fake coverage. Source that refers to coverage
  internals (__gcov, .gcda, GCOV_PREFIX...) makes the measurement
  inconclusive.
- MC/DC is GCC's masking MC/DC over the conditions the compiler sees.
  Language rules can expand one source expression into several conditions
  (Ada string equality, for example), and those count.
- "Decision" coverage is measured as GCC branch coverage without exception
  edges. Every decision outcome is a branch, so 100% branches implies 100%
  decisions; compiler-inserted checks (Ada range checks) are branches too,
  so the figure can be lower than true decision coverage. Justify such
  branches with a waiver that names them.
- Coverage is measured on an instrumented build of the same sources with the
  same tests, not on the release binary.
"""

from __future__ import annotations

import json
import re
from typing import Any

SCRATCH_BUILD = "/scratch/build"
INTERNALS = re.compile(r"__gcov|\bgcov_[a-z]|\.gcda\b|\.gcno\b|GCOV_PREFIX|__llvm_profile")
CODE_SUFFIXES = (".adb", ".ads", ".ada", ".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08", ".c", ".h", ".cc",
                 ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".inc")

# Runs inside the sandbox under FORGE's admitted Python. It receives data only (a JSON file); arguments
# and file names travel as argv lists and paths, never through a shell.
DRIVER = r'''
import base64, json, os, shutil, subprocess, sys

spec = json.load(open(sys.argv[1], encoding="utf-8"))
build = spec["build_dir"]
shutil.copytree("/input", build, dirs_exist_ok=True)
env = dict(spec["env"])
result = {"build": None, "cases": []}


def finish():
    with open("/output/driver.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle)


try:
    done = subprocess.run(spec["compile"], cwd=build, env=env, capture_output=True, timeout=spec["build_timeout_s"])
    result["build"] = {"exit_code": done.returncode, "stderr_tail": done.stderr.decode("utf-8", "replace")[-3000:]}
except subprocess.TimeoutExpired:
    result["build"] = {"exit_code": None, "stderr_tail": "the instrumented build timed out"}
if not result["build"] or result["build"]["exit_code"] != 0:
    finish()
    sys.exit(0)
for case in spec["cases"]:
    case_dir = os.path.join("/scratch/cases", case["case_id"])
    os.makedirs(case_dir)
    for name, data in case["files"].items():
        path = os.path.join(case_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(data))
    argv = [part.replace("{case}", case_dir) for part in spec["entry"]] + \
        [arg.replace("{case}", case_dir) for arg in case["args"]]
    try:
        ran = subprocess.run(argv, cwd=build, env=env, input=base64.b64decode(case["stdin_b64"]),
                             capture_output=True, timeout=spec["case_timeout_s"])
        result["cases"].append({"case_id": case["case_id"], "exit_code": ran.returncode})
    except subprocess.TimeoutExpired:
        result["cases"].append({"case_id": case["case_id"], "timed_out": True})
for root, _dirs, files in os.walk(build):
    for name in files:
        if name.endswith((".gcno", ".gcda")):
            source = os.path.join(root, name)
            target = os.path.join("/output/counters", os.path.relpath(source, build))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
finish()
'''


def integrity_problems(files: dict[str, bytes]) -> list[str]:
    """Source that touches coverage internals could forge its own counters."""
    problems = []
    for path in sorted(files):
        if not path.lower().endswith(CODE_SUFFIXES):
            continue
        for number, line in enumerate(files[path].decode("utf-8", "replace").splitlines(), 1):
            if INTERNALS.search(line):
                problems.append(f"{path}:{number} refers to coverage internals")
                break
    return problems[:20]


def parse_reports(text: str) -> list[dict[str, Any]]:
    """gcov --stdout writes one JSON document per data file, back to back."""
    decoder, documents, position = json.JSONDecoder(), [], 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return documents
        document, position = decoder.raw_decode(text, position)
        documents.append(document)


def _relative(path: str, cwd: str) -> str:
    for prefix in (SCRATCH_BUILD + "/", (cwd.rstrip("/") + "/") if cwd else None):
        if prefix and path.startswith(prefix):
            return path[len(prefix):]
    return path.removeprefix("./")


def summarize(documents: list[dict[str, Any]], snapshot_paths: set[str]) -> dict[str, Any]:
    """Coverage of the snapshot's own files. Code compiled into several units counts once, covered if any covers it."""
    lines: dict[tuple[str, int], bool] = {}
    branches: dict[tuple[str, int, int], bool] = {}
    conditions: dict[tuple[str, int, int], dict[str, Any]] = {}
    excluded: set[str] = set()
    for document in documents:
        cwd = str(document.get("current_working_directory", ""))
        for item in document.get("files", []):
            path = _relative(str(item.get("file", "")), cwd)
            if path not in snapshot_paths:
                if path and path != "<unknown>":
                    excluded.add(path.rsplit("/", 1)[-1])
                continue
            for line in item.get("lines", []):
                number = int(line.get("line_number", 0))
                lines[(path, number)] = lines.get((path, number), False) or int(line.get("count", 0)) > 0
                real = [b for b in line.get("branches", []) if not b.get("throw")]
                for index, branch in enumerate(real):
                    key = (path, number, index)
                    branches[key] = branches.get(key, False) or int(branch.get("count", 0)) > 0
                for index, condition in enumerate(line.get("conditions", [])):
                    key = (path, number, index)
                    known = conditions.get(key)
                    if known is None or int(condition.get("covered", 0)) > known["covered"]:
                        conditions[key] = {"count": int(condition.get("count", 0)),
                                           "covered": int(condition.get("covered", 0)),
                                           "not_covered_true": list(condition.get("not_covered_true", [])),
                                           "not_covered_false": list(condition.get("not_covered_false", []))}

    def percent(covered: int, total: int) -> float:
        return 100.0 if total == 0 else round(100.0 * covered / total, 2)

    per_file: dict[str, dict[str, list[int]]] = {}
    for (path, _), hit in lines.items():
        per_file.setdefault(path, {"statements": [0, 0], "branches": [0, 0], "conditions": [0, 0]})
        per_file[path]["statements"][0] += hit
        per_file[path]["statements"][1] += 1
    for (path, _, _), hit in branches.items():
        per_file[path]["branches"][0] += hit
        per_file[path]["branches"][1] += 1
    for (path, _, _), condition in conditions.items():
        per_file[path]["conditions"][0] += condition["covered"]
        per_file[path]["conditions"][1] += condition["count"]
    totals = {kind: [sum(f[kind][0] for f in per_file.values()), sum(f[kind][1] for f in per_file.values())]
              for kind in ("statements", "branches", "conditions")}
    uncovered_conditions = [{"path": path, "line": line, "not_covered_true": c["not_covered_true"],
                             "not_covered_false": c["not_covered_false"]}
                            for (path, line, _), c in sorted(conditions.items()) if c["covered"] < c["count"]]
    uncovered_lines: dict[str, list[int]] = {}
    for (path, line), hit in sorted(lines.items()):
        if not hit:
            uncovered_lines.setdefault(path, []).append(line)
    return {
        "statement_percent": percent(*totals["statements"]), "decision_percent": percent(*totals["branches"]),
        "mcdc_percent": percent(*totals["conditions"]), "totals": totals, "files": dict(sorted(per_file.items())),
        "uncovered_conditions": uncovered_conditions[:300], "uncovered_lines": {k: v[:200] for k, v in
                                                                              uncovered_lines.items()},
        "uncovered_branch_lines": sorted({f"{p}:{n}" for (p, n, _), hit in branches.items() if not hit})[:300],
        "excluded_generated": sorted(excluded),
        "decision_measure": "GCC branch coverage without exception edges (at least as strict as decision coverage)",
        "mcdc_measure": "GCC 14 masking MC/DC (-fcondition-coverage)",
    }
