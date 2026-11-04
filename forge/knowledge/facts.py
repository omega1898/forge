"""Project facts (ticket D05): what kind of project is this, with proof.

Skills customize themselves from conversation ("we use feature flags, so add
flag checks"). FORGE derives the same facts deterministically from a
snapshot, and every fact names the file that proves it, so checklist
selection is explainable and reproducible. Facts come from file names and a
few small, bounded reads of known manifests; nothing is executed.
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from pathlib import PurePosixPath

from forge.artifacts.blobs import BlobStore
from forge.contracts.base import StrictModel
from forge.workspace.snapshot import Manifest

LANGUAGES = {
    ".py": "python", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".java": "java",
    ".cbl": "cobol", ".cob": "cobol", ".cpy": "cobol", ".ml": "ocaml", ".mli": "ocaml", ".sh": "shell",
    ".ps1": "powershell", ".js": "javascript", ".ts": "typescript", ".rs": "rust", ".go": "go", ".sql": "sql",
}
LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "package-lock.json", "yarn.lock",
             "pnpm-lock.yaml", "Cargo.lock", "go.sum")
FLAG_LIBRARIES = re.compile(rb"\b(launchdarkly|ldclient|unleash|flagsmith|growthbook|feature_flags?)\b")


class Fact(StrictModel):
    name: str
    value: str | bool | int | list[str]
    evidence: list[str]


def detect_facts(manifest: Manifest, blobs: BlobStore) -> list[Fact]:
    paths = [entry.path for entry in manifest.entries]
    by_path = {entry.path: entry for entry in manifest.entries}
    facts: list[Fact] = []

    def read(path: str, limit: int = 256 * 1024) -> bytes:
        entry = by_path[path]
        return b"" if entry.size > limit else blobs.read_bytes(entry.digest, max_bytes=limit)

    counts = Counter(LANGUAGES[PurePosixPath(p).suffix] for p in paths if PurePosixPath(p).suffix in LANGUAGES)
    for language, count in counts.most_common():
        examples = [p for p in paths if LANGUAGES.get(PurePosixPath(p).suffix) == language][:3]
        facts.append(Fact(name=f"language.{language}", value=count, evidence=examples))

    if "pyproject.toml" in by_path:
        try:
            pyproject = tomllib.loads(read("pyproject.toml").decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            pyproject = {}
        facts.append(Fact(name="build.python_packaging", value="pyproject", evidence=["pyproject.toml"]))
        scripts = sorted((pyproject.get("project") or {}).get("scripts", {}))
        if scripts:
            facts.append(Fact(name="entry_points.console_scripts", value=scripts, evidence=["pyproject.toml"]))
        if "pytest" in str(pyproject.get("tool", {})) or "pytest" in str(pyproject.get("project", {})):
            facts.append(Fact(name="tests.framework", value="pytest", evidence=["pyproject.toml"]))
    tests = [p for p in paths if re.search(r"(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$", p)]
    if tests and not any(f.name == "tests.framework" for f in facts):
        facts.append(Fact(name="tests.framework", value="pytest_style", evidence=tests[:3]))
    if tests:
        facts.append(Fact(name="tests.count", value=len(tests), evidence=tests[:3]))
    main_modules = [p for p in paths if p.endswith("__main__.py")]
    if main_modules:
        facts.append(Fact(name="entry_points.module_main", value=main_modules, evidence=main_modules[:3]))

    migrations = [p for p in paths if re.search(r"(^|/)(migrations|alembic)/", p)]
    if migrations:
        facts.append(Fact(name="data.migrations", value=True, evidence=migrations[:3]))
    flags = [p for p in paths if p.endswith(".py") and FLAG_LIBRARIES.search(read(p))]
    if flags:
        facts.append(Fact(name="release.feature_flags", value=True, evidence=flags[:3]))
    ci = [p for p in paths if p.startswith(".github/workflows/") or p in (".gitlab-ci.yml", "Jenkinsfile")]
    if ci:
        facts.append(Fact(name="ci.configured", value=True, evidence=ci[:3]))
    locks = [p for p in paths if PurePosixPath(p).name in LOCKFILES]
    if locks:
        facts.append(Fact(name="dependencies.lockfiles", value=locks, evidence=locks[:3]))
    build = [p for p in paths if PurePosixPath(p).name in ("CMakeLists.txt", "Makefile", "compile_commands.json")]
    if build:
        facts.append(Fact(name="build.native", value=sorted({PurePosixPath(p).name for p in build}), evidence=build[:3]))
    if any(PurePosixPath(p).name == "Dockerfile" for p in paths):
        facts.append(Fact(name="deploy.container", value=True, evidence=[p for p in paths if p.endswith("Dockerfile")][:3]))
    readmes = [p for p in paths if PurePosixPath(p).name.lower().startswith("readme")]
    facts.append(Fact(name="docs.readme", value=bool(readmes), evidence=readmes[:1]))
    return facts


def fact_value(facts: list[Fact], name: str) -> object:
    for fact in facts:
        if fact.name == name:
            return fact.value
    return None
