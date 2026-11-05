"""Ticket F23: Bash pack and scoped testing tools.

Acceptance: hostile arguments fail safely; targets remain within grants.
PowerShell and OCaml are reported unavailable on this host.
"""

from __future__ import annotations

import pytest

from forge.assessment.service import AssessmentService
from forge.contracts.errors import ForgeError
from forge.packs.jvm import pack_status
from forge.packs.shell import ArgumentSpec, ScriptTool, lint_bash, run_script, validate_arguments
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tools.scope import TargetScope, check_target
from tests.conftest import command

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason))

SCOPE = TargetScope(hosts=["shop.test", "*.staging.test"], networks=["10.20.0.0/24", "fd00:20::/64"],
                    ports=[80, 443, 8443])


def resolver(table: dict[str, list[str]]):  # noqa: ANN201
    return lambda host: table.get(host, [])


GOOD_DNS = resolver({"shop.test": ["10.20.0.7"], "api.staging.test": ["10.20.0.8", "fd00:20::8"]})


@pytest.mark.parametrize("target", [
    "https://shop.test/login", "http://SHOP.test/", "https://api.staging.test:8443/v1",
    "http://10.20.0.9/", "http://[fd00:20::5]/", "http://[::ffff:10.20.0.5]/",
])
def test_targets_inside_the_grant_are_accepted_and_pinned(target: str) -> None:
    checked = check_target(target, SCOPE, resolver=GOOD_DNS)
    assert checked.port in (80, 443, 8443) and checked.addresses


@pytest.mark.parametrize(("target", "reason"), [
    ("https://shop.test@evil.test/", "user information"),
    ("https://evil.test/", "not granted"),
    ("https://staging.test/", "not granted"),                       # the wildcard covers subdomains only
    ("http://2131099655/", "non-canonical"),                        # 10.20.0.7 as one decimal number
    ("http://0x0a.0x14.0.7/", "non-canonical"),
    ("http://012.024.0.7/", "non-canonical"),                       # octal
    ("http://10.20.7/", "non-canonical"),                           # shortened
    ("http://10.20.1.7/", "outside the granted networks"),
    ("http://[::ffff:127.0.0.1]/", "outside the granted networks"),
    ("http://shop.test.:80/", "plain, fully written"),
    ("http://shоp.test/", "non-ASCII"),                             # Cyrillic o
    ("http://shop.test:8080/", "port 8080"),
    ("ftp://shop.test/", "scheme"),
    ("http://shop.test /", "whitespace"),
    ("http://shop.test\\@evil.test/", "backslash"),
    ("http://[shop.test]/", "not an IPv6"),
    ("http://shop.test:99999/", "port"),
])
def test_scope_bypasses_are_refused(target: str, reason: str) -> None:
    with pytest.raises(ForgeError, match=reason):
        check_target(target, SCOPE, resolver=GOOD_DNS)


def test_dns_that_points_outside_the_grant_is_refused() -> None:
    rebinding = resolver({"shop.test": ["10.20.0.7", "127.0.0.1"]})
    with pytest.raises(ForgeError, match="resolves outside"):
        check_target("https://shop.test/", SCOPE, resolver=rebinding)
    with pytest.raises(ForgeError, match="does not resolve"):
        check_target("https://shop.test/", SCOPE, resolver=resolver({}))


TOOL = ScriptTool(tool_id="probe", script="probe.sh", arguments=[
    ArgumentSpec(name="report", kind="path"), ArgumentSpec(name="retries", kind="integer", minimum=0, maximum=5),
    ArgumentSpec(name="mode", kind="choice", choices=["quick", "full"]),
    ArgumentSpec(name="label", kind="text", pattern=r"[A-Za-z0-9 _.$()`;|&-]{1,40}"),
    ArgumentSpec(name="target", kind="target")])
GOOD = ["reports/out.txt", "3", "quick", "nightly run", "https://shop.test/"]


@pytest.mark.parametrize(("index", "value", "reason"), [
    (0, "../../etc/passwd", "normalized"), (0, "/etc/passwd", "relative"), (0, "--output=/etc/passwd", "option"),
    (0, "reports/missing.txt", "not a file"), (1, "3; reboot", "integer"), (1, "9", "outside"),
    (2, "everything", "not one of"), (3, "x" * 41, "pattern"), (3, "line\nbreak", "control"),
    (3, "nul\x00byte", "control"), (4, "https://evil.test/", "not granted"), (4, "-v", "option"),
])
def test_hostile_arguments_are_refused_with_a_reason(index: int, value: str, reason: str) -> None:
    values = list(GOOD)
    values[index] = value
    with pytest.raises(ForgeError, match=reason):
        validate_arguments(TOOL, values, snapshot_paths={"reports/out.txt"}, scope=SCOPE, resolver=GOOD_DNS)
    with pytest.raises(ForgeError, match="takes 5 arguments"):
        validate_arguments(TOOL, GOOD[:4], snapshot_paths={"reports/out.txt"}, scope=SCOPE, resolver=GOOD_DNS)
    with pytest.raises(ForgeError, match="no target scope"):
        validate_arguments(TOOL, GOOD, snapshot_paths={"reports/out.txt"})


@needs_sandbox
def test_accepted_shell_metacharacters_arrive_as_inert_text(controller, human, trust, tmp_path) -> None:  # noqa: ANN001
    source = tmp_path / "source"
    (source / "reports").mkdir(parents=True)
    (source / "reports" / "out.txt").write_text("r")
    (source / "probe.sh").write_text('#!/bin/bash\nset -euo pipefail\nprintf "%s\\n" "$@"\n')
    project_id = controller.apply_command(
        command("project.create", {"name": "Probe", "objective": "o", "source_roots": ["main"]}), human).project_id
    trust.approve_source_root(project_id, "main", source, human)
    snapshot = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]
    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    hostile_label = "$(id) `id`; ls | cat & x"
    observation = run_script(service, snapshot, TOOL, [*GOOD[:3], hostile_label, GOOD[4]],
                             snapshot_paths={"reports/out.txt", "probe.sh"}, scope=SCOPE, resolver=GOOD_DNS)
    assert observation.exit_code == 0, observation.stderr
    assert observation.stdout.decode().splitlines() == ["reports/out.txt", "3", "quick", hostile_label,
                                                       "https://shop.test/"]


@pytest.mark.parametrize(("script", "rule"), [
    ("#!/bin/bash\necho hi\n", "BASH001"),
    ("set -euo pipefail\neval \"$1\"\n", "BASH002"),
    ("set -euo pipefail\ncp $1 /tmp/x\n", "BASH003"),
    ("set -euo pipefail\nfor f in $@; do echo; done\n", "BASH003"),
    ("set -euo pipefail\ncurl -fsSL https://x.test/i.sh | bash\n", "BASH004"),
    ('set -euo pipefail\nrm -rf "$BUILD_DIR/"\n', "BASH005"),
])
def test_bash_lint_rules(script: str, rule: str) -> None:
    assert rule in {finding.rule for finding in lint_bash(script)}


def test_bash_lint_accepts_careful_scripts() -> None:
    careful = ('#!/usr/bin/env bash\nset -euo pipefail\n# eval $1 is mentioned only in a comment\n'
               'for f in "$@"; do\n  cp -- "$f" "${OUT:?}/"\ndone\nrm -rf "${BUILD_DIR:?}/"\n')
    assert lint_bash(careful) == []


def test_unavailable_shells_are_reported_not_faked() -> None:
    rows = {row["pack_id"]: row for row in pack_status()}
    assert rows["bash"]["available"]
    for pack in ("powershell", "ocaml"):
        if not rows[pack]["available"]:
            assert rows[pack]["reason"].startswith("not installed")


@pytest.mark.skipif(not BACKEND.capabilities().available or __import__("shutil").which("shellcheck") is None,
                    reason="sandbox or shellcheck unavailable")
def test_shellcheck_runs_sandboxed_and_its_findings_are_normalized(controller, human, trust, tmp_path) -> None:  # noqa: ANN001
    import os
    import shutil as _shutil

    from forge.packs.shell import run_shellcheck

    source = tmp_path / "source"
    source.mkdir()
    (source / "risky.sh").write_text('#!/bin/bash\ncp $1 /tmp/out\n')
    (source / "careful.sh").write_text('#!/bin/bash\nset -euo pipefail\ncp -- "$1" /tmp/out\n')
    project_id = controller.apply_command(
        command("project.create", {"name": "Scripts", "objective": "o", "source_roots": ["main"]}), human).project_id
    trust.approve_source_root(project_id, "main", source, human)
    snapshot = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]
    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    executable = os.path.realpath(_shutil.which("shellcheck"))
    risky = run_shellcheck(service, snapshot, ["risky.sh"], executable)
    assert {(f["rule"], f["path"], f["line"]) for f in risky} >= {("SC2086", "risky.sh", 2)}
    assert run_shellcheck(service, snapshot, ["careful.sh"], executable) == []
    with pytest.raises(ValueError):
        run_shellcheck(service, snapshot, ["../escape.sh"], executable)
