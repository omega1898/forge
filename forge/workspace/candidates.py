"""Candidate snapshots: a base snapshot plus proposed file changes.

A candidate never touches the user's working tree. Proposed files are
stored in the blob store, combined with the base manifest into a new
canonical manifest, and recorded as a snapshot whose parent is the base.
Two different proposals on the same base therefore always have different
identities (INT-T09), and the base stays exactly as it was.
"""

from __future__ import annotations

from forge.artifacts.blobs import BlobStore
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.contracts.versions import SCHEMA_VERSION
from forge.workspace.snapshot import Manifest, ManifestEntry, load_manifest

MAX_CANDIDATE_FILE = 2 * 1024 * 1024


def build_candidate_manifest(
    base: Manifest, blobs: BlobStore, files: dict[str, bytes], *, deletions: tuple[str, ...] = (),
    executable: frozenset[str] = frozenset(),
) -> Manifest:
    entries = {entry.path: entry for entry in base.entries}
    for path in deletions:
        check_relative_path(path)
        entries.pop(path, None)
    for path, data in files.items():
        check_relative_path(path)
        if len(data) > MAX_CANDIDATE_FILE:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"proposed file {path} exceeds {MAX_CANDIDATE_FILE} bytes")
        if any(other.startswith(path + "/") for other in entries) or any(
            path.startswith(other + "/") for other in entries
        ):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"proposed file {path} collides with a directory")
        stored = blobs.put_bytes(data, max_bytes=MAX_CANDIDATE_FILE)
        entries[path] = ManifestEntry(path=path, digest=stored.digest, size=stored.size,
                                      executable=path in executable)
    return Manifest(
        capture_policy_revision=base.capture_policy_revision,
        base_commit=base.base_commit,
        entries=sorted(entries.values(), key=lambda entry: entry.path),
        excluded=base.excluded,
    )


def create_candidate(controller, base_snapshot_id: str, files: dict[str, bytes], *,  # noqa: ANN001
                     deletions: tuple[str, ...] = (), producer_ref: str | None = None) -> dict:
    store, blobs = controller.store, controller.blobs
    base = store.snapshot(base_snapshot_id)
    if base is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{base_snapshot_id} does not exist")
    manifest = build_candidate_manifest(load_manifest(blobs, base.manifest_digest), blobs, files, deletions=deletions)
    stored = blobs.put_bytes(manifest.canonical_bytes())
    receipt = controller.apply_command(
        {"schema_version": SCHEMA_VERSION, "method": "snapshot.record_candidate",
         "project_id": store.project().project_id, "idempotency_key": new_id("request"),
         "params": {"parent_snapshot_id": base_snapshot_id, "manifest_digest": stored.digest,
                    "manifest_size": stored.size, "producer_ref": producer_ref}},
        controller.system_actor,
    )
    return receipt.result
