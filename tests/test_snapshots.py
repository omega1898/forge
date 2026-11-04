"""Ticket F03: artifact store and workspace snapshots.

Acceptance: changing an uncommitted byte changes snapshot identity; forbidden
paths are excluded and explained. Session 2 walkthrough: two candidates from
one base commit have different identities; a frozen snapshot stays unchanged
after the live file changes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from forge.artifacts.blobs import BlobStore
from forge.contracts.commands import ActorContext
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.workspace import snapshot as snapshot_module
from forge.workspace.snapshot import (
    CapturePolicy,
    Manifest,
    capture_tree,
    check_target,
    load_manifest,
    materialize,
    read_git_head,
)
from tests.conftest import command

COMMIT = "a" * 40


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "packet-reader"
    (root / "src").mkdir(parents=True)
    (root / "src" / "reader.py").write_text("def read(data):\n    return []\n")
    (root / "README.md").write_text("# Packet reader\n")
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(COMMIT + "\n")
    return root


@pytest.fixture
def blobs(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path / "blobs")


@pytest.fixture
def project(controller, human, trust, source):  # noqa: ANN001, ANN201
    receipt = controller.apply_command(
        command(
            "project.create",
            {"name": "Packet reader", "objective": "o", "source_roots": ["main"]},
        ),
        human,
    )
    trust.approve_source_root(receipt.project_id, "main", source, human)
    return receipt.project_id


def capture(controller, human, project_id, **params):  # noqa: ANN001, ANN201
    body = {"source_root": "main", **params}
    return controller.apply_command(command("workspace.capture", body, project_id=project_id), human)


# ------------------------------------------------------------ identities --


def test_changing_one_uncommitted_byte_changes_identity(controller, human, project, source) -> None:  # noqa: ANN001
    first = capture(controller, human, project)
    (source / "src" / "reader.py").write_text("def read(data):\n    return [1]\n")
    second = capture(controller, human, project)
    assert first.result["manifest_digest"] != second.result["manifest_digest"]
    snapshots = controller.store.snapshots()
    assert [snap.base_commit for snap in snapshots] == [COMMIT, COMMIT]  # same commit, different bytes


def test_identical_bytes_give_identical_identity(source: Path, blobs: BlobStore) -> None:
    assert capture_tree(source, blobs).manifest_digest == capture_tree(source, blobs).manifest_digest


def test_executable_bit_is_part_of_identity(source: Path, blobs: BlobStore) -> None:
    if os.name == "nt":
        pytest.skip("Windows has no executable bit")
    before = capture_tree(source, blobs).manifest_digest
    os.chmod(source / "src" / "reader.py", 0o755)
    assert capture_tree(source, blobs).manifest_digest != before


def test_frozen_snapshot_survives_live_edit(
    controller, human, project, source, tmp_path  # noqa: ANN001
) -> None:
    receipt = capture(controller, human, project)
    (source / "src" / "reader.py").write_text("LIVE EDIT\n")
    snap = controller.store.snapshot(receipt.result["snapshot_id"])
    manifest = load_manifest(controller.blobs, snap.manifest_digest)
    destination = tmp_path / "job"
    materialize(manifest, controller.blobs, destination)
    assert (destination / "src" / "reader.py").read_text() == "def read(data):\n    return []\n"
    assert not (destination / ".git").exists()


def test_snapshot_records_rebuild_from_events(controller, human, project) -> None:  # noqa: ANN001
    capture(controller, human, project)
    assert controller.store.verify_projections() == []


# ------------------------------------------------------- forbidden paths --


def test_forbidden_paths_are_excluded_and_explained(source: Path, blobs: BlobStore) -> None:
    (source / ".env").write_text("API_KEY=do-not-capture\n")
    (source / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    expected = {
        ".env": "secret_by_policy",
        "deploy.pem": "secret_by_policy",
        "__pycache__": "excluded_by_policy",
        ".git": "excluded_by_policy",
    }
    if os.name != "nt":
        os.symlink("/etc/passwd", source / "passwords")
        os.mkfifo(source / "pipe")
        expected["passwords"] = "symlink_denied"
        expected["pipe"] = "special_file"
    result = capture_tree(source, blobs)
    excluded = {entry.path: entry.reason for entry in result.manifest.excluded}
    assert excluded == expected
    captured = {entry.path for entry in result.manifest.entries}
    assert captured == {"README.md", "src/reader.py"}


def test_secret_bytes_never_reach_the_blob_store(source: Path, blobs: BlobStore) -> None:
    (source / ".env").write_text("API_KEY=do-not-capture\n")
    capture_tree(source, blobs)
    stored = b"".join(path.read_bytes() for path in (blobs.root / "sha256").rglob("*") if path.is_file())
    assert b"do-not-capture" not in stored


@pytest.mark.skipif(sys.platform != "linux", reason="byte filenames are a Linux behavior")
def test_undecodable_and_invalid_names_are_excluded(source: Path, blobs: BlobStore) -> None:
    with open(os.path.join(os.fsencode(source), b"bad-\xff.txt"), "wb") as handle:
        handle.write(b"x")
    (source / "back\\slash.txt").write_text("x")
    reasons = {entry.reason for entry in capture_tree(source, blobs).manifest.excluded}
    assert {"undecodable_name", "invalid_name"} <= reasons


def test_oversized_files_are_excluded_and_file_count_is_bounded(source: Path, blobs: BlobStore) -> None:
    (source / "big.bin").write_bytes(b"x" * 2048)
    small = CapturePolicy(max_file_bytes=1024)
    assert {"path": "big.bin", "reason": "too_large"} in [
        entry.model_dump() for entry in capture_tree(source, blobs, small).manifest.excluded
    ]
    with pytest.raises(ForgeError) as caught:
        capture_tree(source, blobs, CapturePolicy(max_files=1))
    assert caught.value.code == ErrorCode.RESOURCE_UNAVAILABLE


def test_writer_during_capture_is_detected(source: Path, blobs: BlobStore, monkeypatch) -> None:  # noqa: ANN001
    original = snapshot_module._store_file

    def store_then_modify(path: Path, store: BlobStore, max_bytes: int):  # noqa: ANN202
        info = original(path, store, max_bytes)
        # Simulates an editor auto-save racing the capture.
        path.write_text(path.read_text() + "#")
        return info

    monkeypatch.setattr(snapshot_module, "_store_file", store_then_modify)
    with pytest.raises(ForgeError) as caught:
        capture_tree(source, blobs)
    assert caught.value.code == ErrorCode.WORKSPACE_CHANGED


# ------------------------------------------------------- source authority --


def test_unapproved_source_root_is_refused(controller, human, trust, source) -> None:  # noqa: ANN001
    receipt = controller.apply_command(
        command("project.create", {"name": "n", "objective": "o", "source_roots": ["main"]}), human
    )
    with pytest.raises(ForgeError) as caught:
        capture(controller, human, receipt.project_id)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert controller.store.snapshots() == []


def test_undeclared_label_is_rejected(controller, human, project) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError, match="not declared"):
        controller.apply_command(
            command("workspace.capture", {"source_root": "other"}, project_id=project), human
        )


def test_only_trusted_humans_approve_source_roots(trust, source, model_actor) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError) as caught:
        trust.approve_source_root(new_id("project"), "main", source, model_actor)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED


def test_home_and_filesystem_root_are_too_broad(trust, human) -> None:  # noqa: ANN001
    for broad in (Path.home(), Path(Path.home().anchor)):
        with pytest.raises(ForgeError, match="refusing"):
            trust.approve_source_root(new_id("project"), "main", broad, human)


def test_dirty_buffers_are_recorded_not_captured(controller, human, project) -> None:  # noqa: ANN001
    receipt = capture(controller, human, project, dirty_paths=["src/reader.py"])
    assert receipt.result["dirty_paths_not_captured"] == ["src/reader.py"]
    assert controller.store.snapshot(receipt.result["snapshot_id"]).dirty_paths == ["src/reader.py"]


def test_repeated_capture_command_returns_the_same_snapshot(controller, human, project) -> None:  # noqa: ANN001
    raw = command("workspace.capture", {"source_root": "main"}, project_id=project, key="cap-1")
    first = controller.apply_command(raw, human)
    again = controller.apply_command(raw, human)
    assert again.duplicate_delivery and again.result == first.result
    assert len(controller.store.snapshots()) == 1


# ---------------------------------------------------------- blob integrity --


def test_tampered_blob_is_detected_at_materialization(source: Path, blobs: BlobStore, tmp_path: Path) -> None:
    result = capture_tree(source, blobs)
    entry = next(item for item in result.manifest.entries if item.path == "README.md")
    path = blobs._path(entry.digest)
    os.chmod(path, 0o644)
    path.write_text("# tampered\n")
    with pytest.raises(ForgeError) as caught:
        materialize(result.manifest, blobs, tmp_path / "out")
    assert caught.value.details.get("reason") == "integrity"


def test_materialize_refuses_an_existing_destination(source: Path, blobs: BlobStore, tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        materialize(capture_tree(source, blobs).manifest, blobs, tmp_path / "out")


def test_manifest_must_be_canonical(blobs: BlobStore) -> None:
    sloppy = b'{"schema_version": "1.0", "capture_policy_revision": 1, "base_commit": null, "entries": [], "excluded": []}'
    info = blobs.put_bytes(sloppy)
    with pytest.raises(ForgeError, match="canonical"):
        load_manifest(blobs, info.digest)


def test_blob_size_limit(blobs: BlobStore) -> None:
    with pytest.raises(ForgeError):
        blobs.put_bytes(b"x" * 100, max_bytes=10)
    assert not any((blobs.root / "tmp").iterdir())  # no temporary leftovers


# -------------------------------------------------------- portability --


def test_case_collisions_block_windows_and_macos_only(blobs: BlobStore) -> None:
    info = blobs.put_bytes(b"x")
    manifest = Manifest(
        capture_policy_revision=1,
        base_commit=None,
        entries=[
            {"path": "README", "digest": info.digest, "size": 1, "executable": False},
            {"path": "readme", "digest": info.digest, "size": 1, "executable": False},
            {"path": "con.txt", "digest": info.digest, "size": 1, "executable": False},
        ],
        excluded=[],
    )
    assert check_target(manifest, "linux") == []
    assert any("collide" in problem for problem in check_target(manifest, "macos"))
    windows = check_target(manifest, "windows")
    assert any("reserved" in problem for problem in windows)
    assert any("collide" in problem for problem in windows)


@pytest.mark.parametrize(
    "bad", ["", "/etc/passwd", "C:/x", "a/../b", "./a", "a//b", "a\\b", "a\x00b", "a/\x1bb"]
)
def test_relative_path_rules(bad: str) -> None:
    with pytest.raises(ValueError):
        check_relative_path(bad)


def test_manifest_rejects_escaping_paths() -> None:
    with pytest.raises(ValueError):
        Manifest(
            capture_policy_revision=1,
            base_commit=None,
            entries=[{"path": "../../etc/cron.d/x", "digest": "sha256:" + "0" * 64, "size": 1, "executable": False}],
            excluded=[],
        )


# ----------------------------------------------------------------- git --


def test_git_head_variants(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(COMMIT + "\n")
    assert read_git_head(tmp_path) == COMMIT  # detached
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/main\n")
    assert read_git_head(tmp_path) == "b" * 40  # packed
    (git / "HEAD").write_text("ref: ../../../etc/passwd\n")
    assert read_git_head(tmp_path) is None  # traversal refused
    (git / "HEAD").write_text("garbage")
    assert read_git_head(tmp_path) is None


def test_git_is_never_executed(source: Path, blobs: BlobStore, monkeypatch) -> None:  # noqa: ANN001
    import subprocess

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("capture must not run external programs")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert capture_tree(source, blobs).manifest.base_commit == COMMIT
