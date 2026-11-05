"""Tickets F13/F14: the VSCodium extension's logic, tested without an editor.

Acceptance (F13): editor and CLI show the same durable state; reconnect
recovers missed events. (F14): malicious Markdown cannot invoke commands;
keyboard navigation completes a review. The extension is TypeScript with
no third-party dependencies; this test compiles it and runs its tests with
Node, the integration part against a real FORGE API server.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forge.api.client import ForgeClient
from forge.api.launcher import launch
from forge.contracts.clock import FixedClock

EXTENSION = Path(__file__).resolve().parent.parent / "extension"
pytestmark = pytest.mark.skipif(shutil.which("tsc") is None or shutil.which("node") is None,
                                reason="TypeScript compiler or Node.js not installed")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:  # noqa: ANN001
    out = tmp_path_factory.mktemp("extension-build")
    done = subprocess.run(["tsc", "-p", str(EXTENSION), "--outDir", str(out)], capture_output=True, text=True,
                          timeout=300, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    (out / "package.json").write_text((EXTENSION / "package.json").read_text())
    return out


def test_extension_unit_tests_pass(built: Path) -> None:
    # Compiled tests import "../../package.json" relative to out/test.
    shutil.copy(EXTENSION / "package.json", built.parent / "package.json")
    done = subprocess.run(["node", "--test", "--test-reporter=tap", str(built / "test" / "core.test.js")],
                          capture_output=True, text=True,
                          timeout=120, check=False, cwd=built)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    assert "# fail 0" in done.stdout


def summary_ids(summary: dict) -> list[str]:
    order = ["running", "ready", "waiting_on_tasks", "waiting_for_user", "blocked", "pending_decisions", "done",
             "ended", "decided"]
    return [item.get("task_id") or item["decision_id"] for key in order for item in summary[key]["items"]]


def test_editor_reconnects_without_losing_events_and_shows_what_the_cli_reads(built: Path, tmp_path: Path) -> None:
    launched = launch(tmp_path / "forge-root", clock=FixedClock())
    try:
        cli = ForgeClient(launched.server.url, launched.token)
        project = cli.call("project.create", {"idempotency_key": "create", "params": {
            "name": "Packet reader", "objective": "Read packets", "source_roots": ["main"]}})
        environment = {**os.environ, "FORGE_URL": launched.server.url, "FORGE_TOKEN": launched.token,
                       "FORGE_PROJECT_ID": project["project_id"]}
        done = subprocess.run(["node", str(built / "test" / "integration.test.js")], capture_output=True, text=True,
                              timeout=120, check=False, env=environment, cwd=built)
        assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
        [line] = [line for line in done.stdout.splitlines() if line.startswith("FORGE-EXTENSION ")]
        seen = json.loads(line.split(" ", 1)[1])
        summary = cli.call("project.summary", {})
        assert seen["as_of"] == summary["as_of_event_seq"]
        assert seen["applied"] == list(range(1, summary["as_of_event_seq"] + 1))     # every event, once, in order
        assert seen["ids"] == summary_ids(summary) and len(seen["ids"]) >= 2        # same state as the CLI
    finally:
        launched.close()
