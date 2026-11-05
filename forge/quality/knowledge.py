"""Read-only access to the bundled knowledge packs: CWE 5.0, SEI CERT C/C++, and bandit's CWE table.

Every finding FORGE reports is tied, where the evidence allows, to the
weakness it is an instance of (CWE) and to the coding-standard rule it breaks
(CERT). These lookups are deterministic table reads; nothing is inferred.

The packs keep their own notices (MITRE's copyright and license paragraph for
CWE; CC BY 4.0 and MIT attribution for CERT), and `notices()` returns them
for any report or export that includes their content.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from typing import Any

TOP25 = "1435"                      # Weaknesses in the 2025 CWE Top 25
CISQ = {"1306": "CISQ reliability", "1307": "CISQ maintainability", "1308": "CISQ security",
        "1309": "CISQ efficiency"}  # groupings of view 1305 (ISO/IEC 5055-aligned; not a conformance claim)
OWASP_2025 = "1450"                 # Weaknesses in OWASP Top Ten (2025), as CWE 5.0 records it
CERT_TAXONOMY = "CERT C Secure Coding"


def _load(name: str) -> dict[str, Any]:
    return json.loads(resources.files("forge.quality").joinpath(f"data/{name}").read_text("utf-8"))


@cache
def cwe_pack() -> dict[str, Any]:
    return _load("cwe.json")


@cache
def cert_pack() -> dict[str, Any]:
    return _load("cert.json")


@cache
def bandit_cwe() -> dict[str, list[int]]:
    return _load("bandit-cwe.json")


def weakness(cwe: int) -> dict[str, Any] | None:
    return cwe_pack()["weaknesses"].get(str(cwe))


def cwe_name(cwe: int) -> str:
    entry = weakness(cwe)
    return entry["name"] if entry else "unknown weakness"


@cache
def ancestors(cwe: int) -> frozenset[int]:
    """Every parent in the research view (CWE-1000), transitively. Used to roll a specific finding up to a list."""
    found: set[int] = set()
    pending = [cwe]
    while pending:
        entry = weakness(pending.pop())
        for parent in (entry or {}).get("parents", []):
            if int(parent) not in found:
                found.add(int(parent))
                pending.append(int(parent))
    return frozenset(found)


@cache
def view_members(view_id: str) -> frozenset[int]:
    view = cwe_pack()["views"].get(view_id)
    return frozenset(int(m) for m in view["members"]) if view else frozenset()


def in_top25(cwe: int, *, roll_up: bool = True) -> bool:
    members = view_members(TOP25)
    return cwe in members or (roll_up and bool(ancestors(cwe) & members))


def cert_rule(rule_id: str) -> dict[str, Any] | None:
    return cert_pack()["rules"].get(rule_id.upper())


def cert_for_cwe(cwe: int) -> list[tuple[str, str]]:
    """CERT C rules CWE records for this weakness, with the fit grade ("Exact" means equivalent)."""
    entry = weakness(cwe) or {}
    return [(m["id"], m.get("fit") or "unspecified") for m in entry.get("mappings", [])
            if m["taxonomy"] == CERT_TAXONOMY]


def labels(cwes: list[int]) -> list[str]:
    """Standard memberships for a set of weaknesses: Top 25, CISQ measure, OWASP 2025, exact CERT equivalents."""
    found: list[str] = []
    views = cwe_pack()["views"]
    for cwe in sorted(set(cwes)):
        if in_top25(cwe, roll_up=False):
            found.append("CWE Top 25 (2025)")
        for group, name in CISQ.items():
            if cwe in {int(m) for m in views["1305"]["groups"].get(group, {}).get("members", [])}:
                found.append(name)
        for group in views.get(OWASP_2025, {}).get("groups", {}).values():
            category = re.search(r"\bA\d{2}:2025\b", group["name"])
            if str(cwe) in group["members"] and category:
                found.append(f"OWASP {category.group(0)}")
        found.extend(f"CERT {rule}" for rule, fit in cert_for_cwe(cwe) if fit == "Exact")
    return list(dict.fromkeys(found))


def notices() -> list[str]:
    return [cwe_pack()["notice"], cert_pack()["notice"],
            "bandit CWE table derived from PyCQA/bandit (Apache License 2.0)."]
