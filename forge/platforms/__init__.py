"""Truthful platform status (ticket F17).

A check counts for a platform only when it executed natively on that
platform. A Linux VM or WSL reached from a Windows or macOS machine executes
Linux; an emulator executes nothing natively. Evidence records where the
workload ran, and a required platform that no admitted host provides stays
`not_run`, which blocks completion instead of looking green.

Environment names are slugs: an operating system (`linux`, `windows`,
`macos`), optionally with an architecture (`linux_x86_64`, `macos_aarch64`).
"""

from __future__ import annotations

import html
from typing import Any

from forge.contracts.base import StrictModel
from forge.sandbox.base import BackendCapabilities

OPERATING_SYSTEMS = ("linux", "windows", "macos")
ARCH_ALIASES = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}


def normalize_arch(arch: str) -> str:
    lowered = arch.lower()
    return ARCH_ALIASES.get(lowered, lowered)


def environment_of(caps: BackendCapabilities) -> str:
    return f"{caps.os}_{normalize_arch(caps.arch)}"


def parse_environment(name: str) -> tuple[str, str | None]:
    os_name, _, arch = name.partition("_")
    if os_name not in OPERATING_SYSTEMS:
        raise ValueError(f"unknown platform {name!r}; use linux, windows or macos with an optional _arch")
    return os_name, (normalize_arch(arch) if arch else None)


def environment_matches(required: str, recorded: Any) -> bool:
    if not isinstance(recorded, str):
        return False
    want_os, want_arch = parse_environment(required)
    try:
        got_os, got_arch = parse_environment(recorded)
    except ValueError:
        return False
    return got_os == want_os and (want_arch is None or got_arch == want_arch)


def where(caps: BackendCapabilities) -> str:
    """Plain description of where a backend executes, for reasons and reports."""
    text = f"{caps.os} {normalize_arch(caps.arch)}"
    details = []
    if caps.virtualization not in ("none", "unknown"):
        details.append({"vm": "in a virtual machine", "wsl": "in WSL", "container": "in a container",
                         "emulator": "under emulation"}[caps.virtualization])
    if caps.interface_os and caps.interface_os != caps.os:
        details.append(f"reached through {caps.interface_os}")
    return text + (f" ({', '.join(details)})" if details else "")


def native_problem(required: str, caps: BackendCapabilities) -> str | None:
    """Why this backend cannot produce native evidence for `required`, or None if it can."""
    want_os, want_arch = parse_environment(required)
    if not caps.available:
        return f"{caps.backend_id} is unavailable: {caps.unavailable_reason}"
    if caps.os != want_os:
        return f"requires native {required}; this host executes {where(caps)}"
    if want_arch and normalize_arch(caps.arch) != want_arch:
        return f"requires native {required}; this host executes {where(caps)}"
    if caps.virtualization == "emulator":
        return f"requires native {required}; emulated execution is not native evidence"
    return None


def execution_platform(caps: BackendCapabilities) -> dict[str, Any]:
    return {"environment": environment_of(caps), "os": caps.os, "arch": normalize_arch(caps.arch),
            "virtualization": caps.virtualization, "interface_os": caps.interface_os,
            "backend_id": caps.backend_id, "description": where(caps)}


def select_host(required: str, hosts: dict[str, BackendCapabilities]) -> tuple[str | None, dict[str, str]]:
    """The first host (by id) that executes `required` natively, and why each other host was passed over."""
    reasons: dict[str, str] = {}
    for host_id in sorted(hosts):
        problem = native_problem(required, hosts[host_id])
        if problem is None:
            return host_id, reasons
        reasons[host_id] = problem
    return None, reasons


class PlatformRow(StrictModel):
    criterion_id: str
    subject: str
    environment: str
    result: str
    executed_on: str | None
    reason: str


def platform_status(store, snapshot_id: str) -> list[PlatformRow]:  # noqa: ANN001 - RecordReader
    """One row per (criterion, required environment): the latest result for that environment."""
    rows: list[PlatformRow] = []
    assessments = store.assessments(snapshot_id=snapshot_id)
    criterion_ids = sorted({a.criterion_ref.id for a in assessments} | set(_platform_criteria(store)))
    for criterion_id in criterion_ids:
        criterion = store.criterion(criterion_id)
        if criterion is None or not criterion.required_environments:
            continue
        for required in criterion.required_environments:
            matching = [a for a in assessments if a.criterion_ref.id == criterion_id
                        and a.criterion_ref.revision == criterion.revision
                        and environment_matches(required, a.summary.get("environment"))]
            if not matching:
                rows.append(PlatformRow(criterion_id=criterion_id, subject=criterion.subject, environment=required,
                                        result="not_run", executed_on=None,
                                        reason="no compatible host has run this check"))
                continue
            latest = max(matching, key=lambda a: (a.recorded_seq or 0, a.created_at))
            executed = latest.summary.get("executed_on")
            rows.append(PlatformRow(
                criterion_id=criterion_id, subject=criterion.subject, environment=required,
                result=latest.assessment_result, executed_on=executed if isinstance(executed, str) else None,
                reason="; ".join(latest.limitations[:2]) if latest.assessment_result != "pass" else ""))
    return rows


def _platform_criteria(store) -> list[str]:  # noqa: ANN001
    rows = store.conn.execute("SELECT record_id FROM records WHERE record_kind = 'criterion' ORDER BY record_id").fetchall()
    return [row[0] for row in rows]


def render_platform_status(rows: list[PlatformRow]) -> str:
    """Markdown table. Anything not run is shown as not run, never as a pass."""
    def cell(text: str) -> str:
        return html.escape(text, quote=False).replace("|", "\\|").replace("\n", " ")

    lines = ["| Check | Platform | Result | Ran on | Reason |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {cell(row.subject)} | {cell(row.environment)} | **{cell(row.result)}** | "
                     f"{cell(row.executed_on or '-')} | {cell(row.reason or '-')} |")
    return "\n".join(lines) + "\n"
