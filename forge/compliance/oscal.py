"""Import OSCAL catalogs and profiles into compact, verifiable control lists.

OSCAL (Open Security Controls Assessment Language) is NIST's machine-readable
format for control catalogs. FORGE keeps only what it needs to cite a
control exactly: its identifier, its printed label, its title, its group,
its parent (for enhancements) and whether it is withdrawn. Each compact
catalog records where the original came from and the SHA-256 of the exact
bytes it was built from, so every citation in a FORGE report can be traced
back to the authoritative document.

The same importer accepts any OSCAL catalog the user holds, for example a
licensed industry standard converted to OSCAL; FORGE ships only public-domain
NIST catalogs.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.errors import ErrorCode, ForgeError

MAX_OSCAL_BYTES = 64 * 1024 * 1024


class Control(StrictModel):
    control_id: str              # OSCAL id, e.g. "sa-11.1"
    label: str                   # as printed in the standard, e.g. "SA-11(1)"
    title: str
    group: str
    parent: str | None = None
    withdrawn: bool = False


class CompactCatalog(StrictModel):
    catalog_id: str              # FORGE's short name, e.g. "nist-800-53r5"
    title: str
    version: str
    last_modified: str
    source_url: str
    source_sha256: str
    license: str
    controls: list[Control] = Field(default_factory=list)

    def by_label(self) -> dict[str, Control]:
        return {control.label: control for control in self.controls}

    def by_id(self) -> dict[str, Control]:
        return {control.control_id: control for control in self.controls}


def _label(node: dict[str, Any]) -> str:
    labels = [p.get("value", "") for p in node.get("props", []) if p.get("name") == "label"]
    plain = [value for value in labels if value and " " not in value and "class" not in value]
    candidates = [p for p in node.get("props", []) if p.get("name") == "label" and p.get("class") != "zero-padded"]
    if candidates and " " not in candidates[0].get("value", " "):
        return candidates[0]["value"]
    if plain:
        return plain[0]
    match = re.search(r"\(([^()]+)\)\s*$", labels[0]) if labels else None
    return match.group(1) if match else node["id"]


def _withdrawn(node: dict[str, Any]) -> bool:
    return any(p.get("name") == "status" and p.get("value") == "withdrawn" for p in node.get("props", []))


def import_catalog(raw: bytes, *, catalog_id: str, source_url: str, license_note: str) -> CompactCatalog:
    if len(raw) > MAX_OSCAL_BYTES:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "OSCAL file is too large")
    try:
        document = json.loads(raw)
        catalog = document["catalog"]
        metadata = catalog["metadata"]
    except (ValueError, KeyError, TypeError):
        raise ForgeError(ErrorCode.INVALID_REQUEST, "not an OSCAL catalog in JSON form") from None
    controls: list[Control] = []

    def walk_controls(nodes: list[dict[str, Any]], group: str, parent: str | None) -> None:
        for node in nodes:
            if not isinstance(node, dict) or "id" not in node:
                continue
            controls.append(Control(control_id=node["id"], label=_label(node), title=str(node.get("title", ""))[:300],
                                    group=group, parent=parent, withdrawn=_withdrawn(node)))
            walk_controls(node.get("controls", []), group, node["id"])

    def walk_groups(groups: list[dict[str, Any]]) -> None:
        for group in groups:
            name = str(group.get("title", group.get("id", "")))[:200]
            walk_controls(group.get("controls", []), name, None)
            walk_groups(group.get("groups", []))

    walk_groups(catalog.get("groups", []))
    walk_controls(catalog.get("controls", []), "", None)
    if not controls:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "the OSCAL catalog contains no controls")
    return CompactCatalog(catalog_id=catalog_id, title=str(metadata.get("title", ""))[:300],
                          version=str(metadata.get("version", "")), last_modified=str(metadata.get("last-modified", "")),
                          source_url=source_url, source_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
                          license=license_note, controls=controls)


def import_profile_ids(raw: bytes) -> list[str]:
    """Control ids a baseline profile selects (OSCAL include-controls with-ids)."""
    try:
        profile = json.loads(raw)["profile"]
    except (ValueError, KeyError, TypeError):
        raise ForgeError(ErrorCode.INVALID_REQUEST, "not an OSCAL profile in JSON form") from None
    ids: list[str] = []
    for item in profile.get("imports", []):
        for include in item.get("include-controls", []):
            ids.extend(str(value) for value in include.get("with-ids", []))
    return sorted(set(ids))
