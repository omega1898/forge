"""Evidence producers for compliance: inventory (SBOM), secrets, requirements trace, known vulnerabilities.

Each producer reads the snapshot's files as data. None of them runs project
code, so they need no sandbox and work on any platform. Their results are
recorded as execution evidence by ComplianceService.

- `inventory` writes a CycloneDX 1.6 software bill of materials: declared
  third-party packages from lock files and manifests (with package URLs), and
  every file in the snapshot with its SHA-256. Dependencies that are not
  pinned to an exact version are listed, because they cannot be checked
  against a vulnerability database or reproduced.
- `scan_secrets` locates likely credentials using the gateway's redaction
  rules, by file and line. It never copies the secret into the record.
- `trace` follows requirement tags: `Implements: HLR-1` in code and
  `Verifies: HLR-1` in tests, against an optional requirements list.
- `match_vulnerabilities` checks the inventory against OSV records
  (the open format used by osv.dev and GitHub advisories) that the person
  imported. FORGE has no network access, so the database is only as current
  as the last import; the evidence records its identity and size.
"""

from __future__ import annotations

import csv
import io
import json
import re
import tomllib
import uuid
from xml.etree import ElementTree
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from forge.gateway.redaction import redact

MAX_SCAN_BYTES = 4 * 1024 * 1024        # larger files are inventoried by digest but not read as text
SOURCE_SUFFIXES = (".py", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".java", ".adb", ".ads", ".ada", ".f", ".for",
                   ".f77", ".f90", ".f95", ".cbl", ".cob", ".cpy", ".jov", ".j73", ".ml", ".mli", ".s", ".asm", ".sh",
                   ".ps1", ".bas", ".cls", ".vba", ".js", ".ts", ".go", ".rs", ".cs", ".sql")


def is_text(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


def as_text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------- inventory


def _purl(ecosystem: str, name: str, version: str | None) -> str:
    kind = {"PyPI": "pypi", "npm": "npm", "Maven": "maven", "Go": "golang", "crates.io": "cargo",
            "RubyGems": "gem", "NuGet": "nuget"}[ecosystem]
    if ecosystem == "Maven":
        group, _, artifact = name.partition(":")
        path = f"{quote(group, safe='.-_')}/{quote(artifact, safe='.-_')}"
    elif ecosystem == "npm" and name.startswith("@"):
        scope, _, package = name.partition("/")
        path = f"{quote(scope, safe='')}/{quote(package, safe='.-_')}"
    elif ecosystem == "Go":
        path = "/".join(quote(part, safe=".-_~") for part in name.split("/"))
    else:
        path = quote(name, safe=".-_")
    return f"pkg:{kind}/{path}" + (f"@{quote(version, safe='.-_+~')}" if version else "")


def _normal_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _pep508(spec: str) -> tuple[str, str | None] | None:
    match = _PEP508.match(spec.split(";")[0])
    if not match:
        return None
    rest = match.group(3).strip()
    pinned = re.fullmatch(r"===?\s*([A-Za-z0-9.!+_-]+)", rest)
    return _normal_pypi(match.group(1)), pinned.group(1) if pinned else None


def _requirements_txt(text: str) -> list[tuple[str, str | None]]:
    found = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http:", "https:", "file:")) or "://" in line:
            continue
        parsed = _pep508(line)
        if parsed:
            found.append(parsed)
    return found


def _pyproject(text: str) -> list[tuple[str, str | None]]:
    data = tomllib.loads(text)
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    return [parsed for spec in specs if isinstance(spec, str) and (parsed := _pep508(spec))]


def _npm_lock(text: str) -> list[tuple[str, str | None]]:
    data = json.loads(text)
    found = []
    for key, entry in (data.get("packages") or {}).items():
        if key and isinstance(entry, dict) and "node_modules/" in key and entry.get("version"):
            found.append((key.rsplit("node_modules/", 1)[1], str(entry["version"])))
    if not found:
        for name, entry in (data.get("dependencies") or {}).items():
            if isinstance(entry, dict) and entry.get("version"):
                found.append((name, str(entry["version"])))
    return found


def _npm_manifest(text: str) -> list[tuple[str, str | None]]:
    data = json.loads(text)
    found = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (data.get(section) or {}).items():
            exact = str(spec) if re.fullmatch(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]*", str(spec)) else None
            found.append((name, exact))
    return found


def _maven(text: str) -> list[tuple[str, str | None]]:
    root = ElementTree.fromstring(text)
    namespace = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    found = []
    for dependency in root.iter(f"{namespace}dependency"):
        group = dependency.findtext(f"{namespace}groupId") or ""
        artifact = dependency.findtext(f"{namespace}artifactId") or ""
        version = (dependency.findtext(f"{namespace}version") or "").strip()
        if group and artifact:
            found.append((f"{group.strip()}:{artifact.strip()}", version if version and "${" not in version else None))
    return found


def _go_mod(text: str) -> list[tuple[str, str | None]]:
    found, block = [], False
    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        if line.startswith("require ("):
            block = True
            continue
        if block and line == ")":
            block = False
            continue
        parts = line.removeprefix("require ").split() if (block or line.startswith("require ")) else []
        if len(parts) >= 2:
            found.append((parts[0], parts[1]))
    return found


def _cargo_lock(text: str) -> list[tuple[str, str | None]]:
    return [(str(p["name"]), str(p.get("version")) if p.get("version") else None)
            for p in tomllib.loads(text).get("package", []) if "name" in p]


def _gemfile_lock(text: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in re.finditer(r"^    ([A-Za-z0-9_.-]+) \(([^)]+)\)$", text, re.MULTILINE)]


def _nuget(text: str) -> list[tuple[str, str | None]]:
    root = ElementTree.fromstring(text)
    found = []
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag == "package" and element.get("id"):
            found.append((element.get("id"), element.get("version")))
        elif tag == "PackageReference" and element.get("Include"):
            found.append((element.get("Include"), element.get("Version") or element.findtext("Version")))
    return found


def _parser_for(path: str):  # noqa: ANN202
    name = PurePosixPath(path).name.lower()
    if re.fullmatch(r"requirements[\w.-]*\.(txt|in)", name):
        return "PyPI", _requirements_txt
    table = {"pyproject.toml": ("PyPI", _pyproject), "package-lock.json": ("npm", _npm_lock),
             "npm-shrinkwrap.json": ("npm", _npm_lock), "package.json": ("npm", _npm_manifest),
             "pom.xml": ("Maven", _maven), "go.mod": ("Go", _go_mod), "cargo.lock": ("crates.io", _cargo_lock),
             "gemfile.lock": ("RubyGems", _gemfile_lock), "packages.config": ("NuGet", _nuget)}
    if name.endswith((".csproj", ".vbproj", ".fsproj")):
        return "NuGet", _nuget
    return table.get(name)


def inventory(files: dict[str, bytes], digests: dict[str, str], *, subject_name: str, subject_version: str,
              timestamp: str, tool_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(CycloneDX document, summary). `files` holds readable contents; `digests` names every file (path -> sha256:hex).

    Deterministic for the same snapshot except the timestamp."""
    packages: dict[tuple[str, str, str | None], set[str]] = {}
    problems: list[str] = []
    locked_npm = {str(PurePosixPath(p).parent) for p in files if PurePosixPath(p).name in ("package-lock.json",
                                                                                         "npm-shrinkwrap.json")}
    for path in sorted(files):
        parser = _parser_for(path)
        if parser is None or not is_text(files[path]):
            continue
        ecosystem, parse = parser
        if PurePosixPath(path).name == "package.json" and str(PurePosixPath(path).parent) in locked_npm:
            continue                                  # the lock file next to it is authoritative
        try:
            entries = parse(as_text(files[path]))
        except (ValueError, KeyError, TypeError, ElementTree.ParseError, tomllib.TOMLDecodeError) as error:
            problems.append(f"{path}: could not be parsed ({type(error).__name__})")
            continue
        for name, version in entries:
            packages.setdefault((ecosystem, name, version), set()).add(path)
    components: list[dict[str, Any]] = []
    unpinned = []
    for (ecosystem, name, version), sources in sorted(packages.items(), key=lambda item: (item[0][0], item[0][1],
                                                                                          item[0][2] or "")):
        purl = _purl(ecosystem, name, version)
        component: dict[str, Any] = {"type": "library", "bom-ref": purl, "name": name, "purl": purl,
                                     "properties": [{"name": "forge:ecosystem", "value": ecosystem}] +
                                     [{"name": "forge:declared_in", "value": s} for s in sorted(sources)]}
        if version:
            component["version"] = version
        else:
            unpinned.append(f"{ecosystem}:{name}")
        components.append(component)
    file_components = [{"type": "file", "bom-ref": f"file:{path}", "name": path,
                        "hashes": [{"alg": "SHA-256", "content": digest.removeprefix("sha256:")}]}
                       for path, digest in sorted(digests.items())]
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "forge-sbom:" + subject_version + ":" + json.dumps(
        [c["bom-ref"] for c in components + file_components]))
    document = {
        "bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{serial}", "version": 1,
        "metadata": {"timestamp": timestamp,
                     "tools": {"components": [{"type": "application", "name": "forge", "version": tool_version}]},
                     "component": {"type": "application", "bom-ref": "subject", "name": subject_name,
                                   "version": subject_version}},
        "components": components + file_components,
    }
    summary = {"components": len(components), "files": len(file_components), "unpinned": unpinned[:200],
               "ecosystems": sorted({c["properties"][0]["value"] for c in components}), "problems": problems[:50]}
    return document, summary


def components_of(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Library components of a CycloneDX document as (ecosystem, name, version) records."""
    found = []
    for component in document.get("components", []):
        if component.get("type") != "library":
            continue
        properties = {p["name"]: p["value"] for p in component.get("properties", []) if "name" in p}
        found.append({"ecosystem": properties.get("forge:ecosystem", ""), "name": component.get("name", ""),
                      "version": component.get("version"), "purl": component.get("purl", "")})
    return found


# ------------------------------------------------------------------ secrets


def scan_secrets(files: dict[str, bytes]) -> tuple[list[dict[str, Any]], int]:
    """(findings, files scanned). Each finding names the file, line and kind, never the value."""
    findings, scanned = [], 0
    for path in sorted(files):
        data = files[path]
        if len(data) > MAX_SCAN_BYTES or not is_text(data):
            continue
        scanned += 1
        for number, line in enumerate(as_text(data).splitlines(), 1):
            _, kinds = redact(line)
            if kinds:
                findings.append({"path": path, "line": number, "kinds": sorted(set(kinds))})
    return findings, scanned


# -------------------------------------------------------------------- trace

_TAG = re.compile(r"\b(Implements|Satisfies|Verifies|Tests)\s*:\s*([A-Za-z][\w.-]*(?:\s*,\s*[A-Za-z][\w.-]*)*)")
_REQUIREMENT_ID = re.compile(r"^[A-Za-z][\w.-]*$")


def read_requirements(data: bytes) -> list[str]:
    """Requirement ids from a CSV (first column, header optional) or a plain list, one id per line."""
    ids: list[str] = []
    for row in csv.reader(io.StringIO(as_text(data))):
        if not row or not row[0].strip() or row[0].strip().startswith("#"):
            continue
        value = row[0].strip()
        if value.lower() in ("id", "requirement", "requirement_id", "req"):
            continue
        if _REQUIREMENT_ID.match(value) and value not in ids:
            ids.append(value)
    return ids


def trace(files: dict[str, bytes], requirements: list[str] | None = None) -> dict[str, Any]:
    """Which requirements are implemented by code and verified by tests, and where the trace breaks."""
    implemented: dict[str, list[str]] = {}
    verified: dict[str, list[str]] = {}
    untraced: list[str] = []
    for path in sorted(files):
        data = files[path]
        if len(data) > MAX_SCAN_BYTES or not is_text(data):
            continue
        tagged = False
        for number, line in enumerate(as_text(data).splitlines(), 1):
            for match in _TAG.finditer(line):
                tagged = True
                target = implemented if match.group(1) in ("Implements", "Satisfies") else verified
                for requirement in re.split(r"\s*,\s*", match.group(2).strip()):
                    target.setdefault(requirement, []).append(f"{path}:{number}")
        if not tagged and path.lower().endswith(SOURCE_SUFFIXES):
            untraced.append(path)
    declared = list(requirements) if requirements is not None else sorted(set(implemented) | set(verified))
    gaps = []
    for requirement in declared:
        if requirement not in implemented:
            gaps.append({"requirement": requirement, "problem": "no code is tagged as implementing it"})
        if requirement not in verified:
            gaps.append({"requirement": requirement, "problem": "no test is tagged as verifying it"})
    if requirements is not None:
        for requirement in sorted((set(implemented) | set(verified)) - set(requirements)):
            gaps.append({"requirement": requirement, "problem": "tagged in the code but not in the requirements list"})
    return {"requirements": declared, "requirements_source": "list" if requirements is not None else "tags",
            "implemented_by": {k: v[:20] for k, v in sorted(implemented.items())},
            "verified_by": {k: v[:20] for k, v in sorted(verified.items())},
            "gaps": gaps[:500], "untraced_source_files": untraced[:200]}


# ---------------------------------------------------------- vulnerabilities

VERSION_PARTS = 24
_PRE = {"dev": -3, "a": -2, "alpha": -2, "b": -2, "beta": -2, "c": -1, "rc": -1, "pre": -1, "preview": -1}
_POST = {"post": 1, "rev": 1, "r": 1, "p": 1}


def version_key(version: str) -> tuple[tuple[int, int, str], ...]:
    """Best-effort ordering for PEP 440 / SemVer style versions; exact version lists are preferred when present."""
    tokens = re.findall(r"\d+|[A-Za-z]+", version.strip().lstrip("vV").split("+")[0])
    key: list[tuple[int, int, str]] = []
    for token in tokens:
        if token.isdigit():
            key.append((0, int(token), ""))
        else:
            lowered = token.lower()
            key.append((_PRE.get(lowered, _POST.get(lowered, -2)), 0, lowered))
    # Pad to a fixed length so a missing part compares as zero: 1.0 == 1.0.0, and 1.0a1 < 1.0 < 1.0.post1.
    return tuple((key + [(0, 0, "")] * VERSION_PARTS)[:VERSION_PARTS])


def _compare(left: str, right: str) -> int:
    a, b = version_key(left), version_key(right)
    return (a > b) - (a < b)


def _in_range(version: str, events: list[dict[str, str]]) -> bool:
    """OSV range evaluation: walk events in version order up to the candidate."""
    def position(event: dict[str, str]) -> str:
        return next(iter(event.values()))
    affected = False
    for event in sorted(events, key=lambda e: version_key("0" if position(e) == "0" else position(e))):
        kind, value = next(iter(event.items()))
        if kind == "introduced" and (value == "0" or _compare(value, version) <= 0):
            affected = True
        elif kind == "fixed" and _compare(value, version) <= 0:
            affected = False
        elif kind == "last_affected" and _compare(value, version) < 0:
            affected = False
    return affected


def _normal_name(ecosystem: str, name: str) -> str:
    return _normal_pypi(name) if ecosystem == "PyPI" else name.lower() if ecosystem in ("NuGet",) else name


def match_vulnerabilities(components: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for record in records:
        for affected in record.get("affected", []) or []:
            package = affected.get("package") or {}
            ecosystem, name = str(package.get("ecosystem", "")).split(":")[0], str(package.get("name", ""))
            index.setdefault((ecosystem, _normal_name(ecosystem, name)), []).append((record, affected))
    hits, unchecked = [], []
    for component in components:
        ecosystem, name, version = component["ecosystem"], component["name"], component.get("version")
        if not version:
            unchecked.append(f"{ecosystem}:{name} (no exact version)")
            continue
        for record, affected in index.get((ecosystem, _normal_name(ecosystem, name)), []):
            how = None
            if version in [str(v) for v in affected.get("versions", []) or []]:
                how = "listed version"
            else:
                for spec in affected.get("ranges", []) or []:
                    if spec.get("type") in ("SEMVER", "ECOSYSTEM") and _in_range(version, spec.get("events", [])):
                        how = f"{spec['type'].lower()} range"
                        break
            if how:
                hits.append({"component": f"{ecosystem}:{name}", "version": version, "id": record.get("id", "?"),
                             "aliases": list(record.get("aliases", []) or [])[:10],
                             "summary": str(record.get("summary", ""))[:300], "matched_by": how})
    return {"affected": hits[:500], "components": len(components), "unchecked": unchecked[:200]}
