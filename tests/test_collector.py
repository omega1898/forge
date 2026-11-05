"""Ticket F04: the output collector (EX-T07).

Acceptance: an output tree containing traversal, links, case collisions or
excessive expansion is rejected before anything enters the trusted store.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from forge.artifacts.blobs import BlobStore
from forge.execution.collector import collect_outputs


@pytest.fixture
def blobs(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path / "blobs")


@pytest.fixture
def out(tmp_path: Path) -> Path:
    directory = tmp_path / "out"
    (directory / "reports").mkdir(parents=True)
    (directory / "reports" / "result.json").write_text('{"passed": 3}')
    (directory / "log.txt").write_text("ok\n")
    return directory


def stored_blobs(blobs: BlobStore) -> list[Path]:
    return [path for path in (blobs.root / "sha256").rglob("*") if path.is_file()]


def collect(out: Path, blobs: BlobStore, **limits):  # noqa: ANN003, ANN201
    return collect_outputs(out, blobs, **{"max_files": 100, "max_bytes": 1_000_000, **limits})


def test_clean_output_is_imported(out: Path, blobs: BlobStore) -> None:
    result = collect(out, blobs)
    assert result.accepted
    assert [item.path for item in result.files] == ["log.txt", "reports/result.json"]
    assert len(stored_blobs(blobs)) == 2


@pytest.mark.skipif(os.name == "nt", reason="links and FIFOs need POSIX")
@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda d: os.symlink("/etc/passwd", d / "escape"), "symlink"),
        (lambda d: os.link(d / "log.txt", d / "hardlinked"), "hard_link"),
        (lambda d: os.mkfifo(d / "pipe"), "special_file"),
        (lambda d: ((d / "README").write_text("a"), (d / "readme").write_text("b")), "name_collision"),
    ],
)
def test_hostile_entries_reject_the_whole_import(out: Path, blobs: BlobStore, make, reason: str) -> None:  # noqa: ANN001
    if reason == "name_collision":
        (out / "CaseProbe").write_text("x")
        case_insensitive = (out / "caseprobe").exists()
        (out / "CaseProbe").unlink()
        if case_insensitive:
            pytest.skip("this filesystem cannot hold names differing only in case, so the collision cannot occur")
    make(out)
    result = collect(out, blobs)
    assert not result.accepted
    assert reason in {entry.reason for entry in result.rejected}
    assert stored_blobs(blobs) == []  # nothing reached the trusted store


def test_excessive_expansion_is_rejected(out: Path, blobs: BlobStore) -> None:
    for number in range(20):
        (out / f"f{number}.txt").write_text("x")
    assert "too_many_files" in {entry.reason for entry in collect(out, blobs, max_files=10).rejected}
    (out / "big.bin").write_bytes(b"x" * 5000)
    assert "too_large" in {entry.reason for entry in collect(out, blobs, max_bytes=4000).rejected}
    assert stored_blobs(blobs) == []


def test_deep_nesting_is_rejected(out: Path, blobs: BlobStore) -> None:
    deep = out
    for level in range(20):
        deep = deep / f"d{level}"
    deep.mkdir(parents=True)
    (deep / "x").write_text("x")
    assert "too_deep" in {entry.reason for entry in collect(out, blobs).rejected}


@pytest.mark.skipif(os.name == "nt", reason="backslash is a separator on Windows")
def test_names_that_are_not_clean_relative_paths_are_rejected(out: Path, blobs: BlobStore) -> None:
    (out / "a\\b").write_text("x")
    assert "invalid_name" in {entry.reason for entry in collect(out, blobs).rejected}
