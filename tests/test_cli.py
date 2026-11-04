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
