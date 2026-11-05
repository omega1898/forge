"""Build FORGE's bundled knowledge packs from their authoritative sources (run by maintainers, not at runtime).

    python -m forge.quality.packs cwe  path/to/cwec_v5.0.xml.zip  forge/quality/data/cwe.json
    python -m forge.quality.packs cert path/to/secure-coding-standards  forge/quality/data/cert.json

CWE (MITRE, terms of use: free for commercial use provided MITRE's copyright
and the license paragraph travel with every copy): every weakness with its
name, abstraction, mapping-usage guidance, parents in the research view
(1000), graded taxonomy mappings (CERT C, CERT Java, CISQ/OMG, ISA/IEC 62443,
OpenSSF Python...), and every view flattened to its weakness IDs (2025 Top 25,
CISQ measures, CERT, OWASP...).

SEI CERT C and C++ (Carnegie Mellon University; text CC BY 4.0, code examples
MIT, since 2026): every rule with its title, first paragraph, risk assessment
(severity, likelihood, detectable, repairable, priority, level), CWE tags,
the open-source checkers its own "Automated Detection" table names, and the
first compliant code example, which FORGE hands to a model repairing that rule.

Both packs record the source and the SHA-256 of what they were built from.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

CWE_SOURCE = "https://raw.githubusercontent.com/lmodel/cwe/main/upstream/cwec_v5.0.xml.zip"
CWE_NOTICE = ("CWE is a trademark of The MITRE Corporation. Copyright (c) The MITRE Corporation. "
              "CWE is free to use by any organization or individual for any research, development, and/or "
              "commercial purposes, per the CWE Terms of Use. Accordingly, The MITRE Corporation hereby grants you "
              "a non-exclusive, royalty-free license to use CWE for research, development, and commercial "
              "purposes. Any copy you make for such purposes is authorized on the condition that you reproduce "
              "MITRE's copyright designation and this license in any such copy.")
CERT_SOURCE = "https://github.com/cmu-sei/secure-coding-standards"
CERT_NOTICE = ("SEI CERT C and C++ Coding Standards (c) 2026 Carnegie Mellon University, licensed under the "
               "Creative Commons Attribution 4.0 International License; code examples are licensed under the MIT "
               "License. CERT and Carnegie Mellon are registered trademarks of Carnegie Mellon University; their "
               "use here does not imply endorsement by Carnegie Mellon University.")
OPEN_DETECTORS = {"clang": "clang", "cppcheck": "cppcheck", "gcc": "gcc", "frama-c": "frama-c", "infer": "infer",
                  "splint": "splint", "ikos": "ikos", "flawfinder": "flawfinder"}


def _plain(text: str) -> str:
    """Markdown/HTML fragment to one line of plain text."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("\\[", "[").replace("\\]", "]").replace("**", "").replace("`", "")
    return " ".join(text.split())


# --------------------------------------------------------------------- CWE


def build_cwe(zip_path: Path) -> dict[str, Any]:
    raw = zip_path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".xml"))
        root = ElementTree.fromstring(archive.read(name))
    ns = root.tag.split("}")[0] + "}"

    def children(node, tag: str) -> list:  # noqa: ANN001
        found = None if node is None else node.find(f"{ns}{tag}")
        return [] if found is None else list(found)

    def text(node, tag: str) -> str:  # noqa: ANN001
        found = node.find(f"{ns}{tag}")
        return "" if found is None or found.text is None else " ".join(found.text.split())

    weaknesses: dict[str, Any] = {}
    for node in root.find(f"{ns}Weaknesses"):
        parents = [r.get("CWE_ID") for r in children(node, "Related_Weaknesses")
                   if r.get("Nature") == "ChildOf" and r.get("View_ID") == "1000"]
        mappings = []
        for mapping in children(node, "Taxonomy_Mappings"):
            entry = text(mapping, "Entry_ID")
            if entry:
                mappings.append({"taxonomy": mapping.get("Taxonomy_Name"), "id": entry,
                                 "name": text(mapping, "Entry_Name")[:160], "fit": text(mapping, "Mapping_Fit") or None})
        notes = node.find(f"{ns}Mapping_Notes")
        weaknesses[node.get("ID")] = {
            "name": node.get("Name"), "abstraction": node.get("Abstraction"), "status": node.get("Status"),
            "usage": text(notes, "Usage") if notes is not None else None,
            "summary": text(node, "Description")[:400], "parents": parents, "mappings": mappings}
    groupings: dict[str, Any] = {}
    for node in root.find(f"{ns}Groupings"):
        members = [m.get("CWE_ID") for m in children(node, "Relationships") if m.tag == f"{ns}Has_Member"]
        groupings[node.get("ID")] = {"name": node.get("Name"), "status": node.get("Status"), "members": members}

    def flatten(ids: list[str], seen: set[str]) -> list[str]:
        out: list[str] = []
        for cwe_id in ids:
            if cwe_id in seen:
                continue
            seen.add(cwe_id)
            if cwe_id in groupings:
                out.extend(flatten(groupings[cwe_id]["members"], seen))
            elif cwe_id in weaknesses:
                out.append(cwe_id)
        return out

    views: dict[str, Any] = {}
    for node in root.find(f"{ns}Views"):
        members = [m.get("CWE_ID") for m in children(node, "Members")]
        views[node.get("ID")] = {"name": node.get("Name"), "type": node.get("Type"),
                                 "groups": {g: {"name": groupings[g]["name"], "members": flatten([g], set())}
                                            for g in members if g in groupings},
                                 "members": sorted(set(flatten(members, set())), key=int)}
    return {"catalog": "CWE", "version": root.get("Version"), "date": root.get("Date"),
            "source": CWE_SOURCE, "source_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "notice": CWE_NOTICE, "weaknesses": weaknesses,
            "groupings": {k: {"name": v["name"], "status": v["status"]} for k, v in groupings.items()},
            "views": views}


# -------------------------------------------------------------------- CERT

_TITLE = re.compile(r"^#\s+([A-Z]{3}\d{2}-(?:C|CPP))\.\s+(.+)$", re.M)
_RISK = re.compile(r"^\|\s*([A-Z]{3}\d{2}-(?:C|CPP))\s*\|(.+)\|\s*$", re.M)


def _section(body: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    return match.group(1) if match else ""


def _rule(path: Path, standard: str, root: Path) -> dict[str, Any] | None:
    body = path.read_text(encoding="utf-8")
    title = _TITLE.search(body)
    if title is None:
        return None
    rule_id, name = title.group(1), _plain(title.group(2))
    front = body.split("---", 2)[1] if body.startswith("---") else ""
    cwes = sorted({int(m) for m in re.findall(r"^\s*-\s*cwe-(\d+)\s*$", front, re.M)})
    after = body[title.end():].strip().split("\n\n")
    summary = _plain(next((p for p in after if p.strip() and not p.lstrip().startswith(("|", "::", ">", "#"))), ""))
    risk: dict[str, Any] = {}
    match = next((m for m in _RISK.finditer(body) if m.group(1) == rule_id), None)
    if match is not None:
        cells = [_plain(c) for c in match.group(2).split("|")]
        if len(cells) >= 6:
            risk = {"severity": cells[0], "likelihood": cells[1], "detectable": cells[2] == "Yes",
                    "repairable": cells[3] == "Yes", "priority": cells[4], "level": cells[5]}
    detectors = []
    for row in _section(body, "Automated Detection").splitlines():
        parts = [p.strip() for p in row.strip().strip("|").split("|")]
        if len(parts) < 3:
            continue
        tool = _plain(parts[0]).lower()
        if tool in OPEN_DETECTORS:
            checkers = [c for c in re.findall(r"\*\*([^*]+)\*\*", parts[2])] or [_plain(parts[2])]
            for checker in checkers:
                for item in re.split(r"<br\s*/?>|,\s*", checker):
                    item = _plain(item)
                    if item:
                        detectors.append({"tool": OPEN_DETECTORS[tool], "checker": item[:120],
                                          "note": _plain(parts[3])[:160] if len(parts) > 3 else ""})
    example = ""
    solution = _section(body, "Compliant Solution")
    code = re.search(r"```[^\n]*\n(.*?)```", solution, re.S)
    if code:
        example = code.group(1).rstrip()[:1500]
    relative = path.relative_to(root).with_suffix("").parts
    url_parts = [re.sub(r"^\d+\.", "", part) for part in relative]
    return {"id": rule_id, "standard": standard, "title": name, "summary": summary[:500], "cwe": cwes,
            "risk": risk, "detectors": detectors, "compliant_example": example,
            "url": "https://cmu-sei.github.io/secure-coding-standards/" + "/".join(url_parts),
            "source_path": path.relative_to(root.parent).as_posix()}


def build_cert(repo: Path) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    for folder, standard in (("content/4.sei-cert-c-coding-standard", "cert-c"),
                             ("content/5.sei-cert-cpp-coding-standard", "cert-cpp")):
        for path in sorted((repo / folder).rglob("*.md")):
            if "rules" not in path.parts[-3:-1] and not any("rules" in p for p in path.parts):
                continue
            rule = _rule(path, standard, repo / "content")
            if rule is not None:
                rules[rule["id"]] = rule
    digest = hashlib.sha256()
    for path in sorted(repo.glob("content/[45].*/**/*.md")):
        digest.update(path.relative_to(repo).as_posix().encode() + b"\0" + path.read_bytes())
    return {"catalog": "SEI CERT C and C++ Coding Standards", "source": CERT_SOURCE,
            "source_sha256": "sha256:" + digest.hexdigest(), "notice": CERT_NOTICE, "rules": rules}


def main(argv: list[str]) -> int:
    kind, source, target = argv
    pack = build_cwe(Path(source)) if kind == "cwe" else build_cert(Path(source))
    Path(target).write_text(json.dumps(pack, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {target}: {len(pack.get('weaknesses', pack.get('rules', {})))} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
