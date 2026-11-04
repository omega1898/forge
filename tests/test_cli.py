"""The development CLI runs the Session 1 walkthrough end to end."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.cli import main


def test_demo_inspect_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "forge-data"
    assert main(["demo", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "duplicate_delivery = True" in output
    assert "Client B was refused: stale_version" in output
    assert "differences: none" in output

    assert main(["inspect", "--root", str(root)]) == 0
    assert "#4 task.status_changed" in capsys.readouterr().out
    assert main(["verify", "--root", str(root)]) == 0
    # Running the demo twice into the same root is refused, not merged.
    assert main(["demo", "--root", str(root)]) == 2
    capsys.readouterr()


def test_summary_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    root = tmp_path / "forge-data"
    assert main(["demo", "--root", str(root)]) == 0
    assert "### Blockers" in capsys.readouterr().out

    assert main(["summary", "--root", str(root)]) == 0
    text = capsys.readouterr().out
    assert "Blocked: Define the packet format and boundary cases" in text

    assert main(["summary", "--root", str(root), "--as-of", "3", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["as_of_event_seq"] == 3
    assert report["blocked"]["total"] == 0  # the task was only blocked at event 4
    assert report["ready"]["total"] == 1

    assert main(["summary", "--root", str(root), "--as-of", "999"]) == 1
    assert "invalid_request" in capsys.readouterr().out
