"""Apply an approved proposal to the user's working tree, safely.

This is the only code in FORGE that writes into the user's project folder.

Order of operations:
1. the proposal digest presented must match the recorded proposal;
2. authority: an approved apply_change decision bound to this exact digest,
   or (for implementation proposals only) an effective grant allowing
   apply_change on every changed path;
3. conflict check BEFORE any write: every destination must still hold the
   bytes the proposal was based on (absent for additions). Any difference
   means the user edited the file: refuse with workspace_conflict and touch
   nothing; the candidate stays in the store, the user's edit stays on disk;
4. journal: write a recovery journal, back up originals (they are already in
   the blob store by digest), stage every new file next to its destination;
5. replace files one by one with atomic renames, marking each in the journal;
6. mark the journal committed, then record the application.

A multi-file change is not one atomic operation; the journal makes a crash
recoverable: recover() rolls back any uncommitted application to the
original bytes, and records committed-but-unrecorded ones.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from forge.contracts.commands import ActorContext
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.contracts.versions import SCHEMA_VERSION


def _file_digest(path: Path) -> str | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        return "not-a-regular-file"
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def _safe_destination(root: Path, relative: str) -> Path:
    """Resolve a project path, refusing symlinked components and escapes."""
    check_relative_path(relative)
    current = root
    for part in relative.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            raise ForgeError(ErrorCode.PERMISSION_DENIED, f"{relative}: a directory in its path is a symlink")
        if current.exists() and not current.is_dir():
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{relative}: a parent path is not a directory")
    destination = root / relative
    if destination.is_symlink():
        raise ForgeError(ErrorCode.PERMISSION_DENIED, f"{relative} is a symlink; refusing to write through it")
    if not str(destination.resolve().parent).startswith(str(root.resolve())):
        raise ForgeError(ErrorCode.PERMISSION_DENIED, f"{relative} resolves outside the project")
    return destination


class ApplyService:
    def __init__(self, controller, journal_dir: Path) -> None:  # noqa: ANN001
        self.controller = controller
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    def _record(self, proposal_id: str, apply_id: str, outcome: str, detail: dict[str, Any]) -> None:
        self.controller.apply_command(
            {"schema_version": SCHEMA_VERSION, "method": "change.record_application",
             "project_id": self.controller.store.project().project_id, "idempotency_key": f"apply-{apply_id}-{outcome}",
             "params": {"proposal_id": proposal_id, "apply_id": apply_id, "outcome": outcome, "detail": detail}},
            self.controller.system_actor,
        )

    def _authorized(self, proposal: dict[str, Any], actor: ActorContext) -> str:
        for decision in self.controller.store.decisions(disposition="approved"):
            if (decision.decision_kind == "apply_change" and decision.subject_ref.id == proposal["proposal_id"]
                    and decision.proposal_digest == proposal["proposal_digest"]):
                return decision.decision_id
        trust = self.controller.trust_store
        if proposal["kind"] == "implementation" and trust is not None and actor.kind in ("human", "system"):
            paths = [change["path"] for change in proposal["changes"]]
            project_id = self.controller.store.project().project_id
            for grant in trust.effective_grants(project_id):
                covers_all = all(any(p == scope or p.startswith(scope + "/") for scope in grant.path_scope)
                                 for p in paths)
                if "apply_change" in grant.actions and covers_all:
                    return grant.grant_id
        raise ForgeError(
            ErrorCode.PERMISSION_DENIED,
            "applying this proposal needs an approved apply_change decision for its exact digest"
            + ("" if proposal["kind"] == "test_change" else " or a grant covering every changed path"),
        )

    def apply(self, proposal_id: str, proposal_digest: str, actor: ActorContext) -> dict[str, Any]:
        store, blobs = self.controller.store, self.controller.blobs
        proposal = store.proposal(proposal_id)
        if proposal is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{proposal_id} does not exist")
        if proposal["proposal_digest"] != proposal_digest:
            raise ForgeError(ErrorCode.STALE_VERSION, "the presented digest is not this proposal's digest; review again")
        if proposal["status"] != "open":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{proposal_id} is already {proposal['status']}")
        authority = self._authorized(proposal, actor)
        base = store.snapshot(proposal["base_snapshot_id"])
        root = self.controller.trust_store.source_root(store.project().project_id, base.source_root)
        if root is None:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, f"source root {base.source_root!r} is not approved")

        apply_id = new_id("request").removeprefix("request_")
        conflicts = []
        destinations: dict[str, Path] = {}
        for change in proposal["changes"]:
            destination = _safe_destination(root, change["path"])
            destinations[change["path"]] = destination
            current = _file_digest(destination)
            if current != change["base_digest"]:
                conflicts.append({"path": change["path"], "expected": change["base_digest"], "found": current})
        if conflicts:
            self._record(proposal_id, apply_id, "conflict", {"conflicts": conflicts})
            raise ForgeError(
                ErrorCode.WORKSPACE_CHANGED,
                f"workspace_conflict: {len(conflicts)} file(s) changed since the proposal was made; "
                "nothing was written, your edits and the candidate are both preserved",
                details={"reason": "workspace_conflict", "conflicts": conflicts},
            )

        journal = {"apply_id": apply_id, "proposal_id": proposal_id, "root": str(root), "state": "prepared",
                   "operations": proposal["changes"], "done": []}
        journal_path = self.journal_dir / f"{apply_id}.json"
        self._write_journal(journal_path, journal)
        staged: dict[str, Path] = {}
        for change in proposal["changes"]:
            if change["new_digest"] is None:
                continue
            destination = destinations[change["path"]]
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = destination.with_name(f".{destination.name}.forge-stage-{apply_id}")
            with open(stage, "wb") as handle:
                for chunk in blobs.iter_verified(change["new_digest"]):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            staged[change["path"]] = stage
        for change in proposal["changes"]:
            destination = destinations[change["path"]]
            if change["new_digest"] is None:
                destination.unlink()
            else:
                os.replace(staged[change["path"]], destination)
            journal["done"].append(change["path"])
            self._write_journal(journal_path, journal)
        journal["state"] = "committed"
        self._write_journal(journal_path, journal)
        self._record(proposal_id, apply_id, "applied", {"authority": authority,
                                                        "paths": [c["path"] for c in proposal["changes"]]})
        journal_path.unlink()
        return {"apply_id": apply_id, "outcome": "applied", "authority": authority,
                "paths": [c["path"] for c in proposal["changes"]]}

    @staticmethod
    def _write_journal(path: Path, journal: dict[str, Any]) -> None:
        temp = path.with_suffix(".tmp")
        with open(temp, "w") as handle:
            json.dump(journal, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def recover(self) -> list[dict[str, Any]]:
        """Finish or undo applications interrupted by a crash."""
        outcomes = []
        for journal_path in sorted(self.journal_dir.glob("*.json")):
            journal = json.loads(journal_path.read_text())
            root = Path(journal["root"])
            if journal["state"] == "committed":
                self._record(journal["proposal_id"], journal["apply_id"], "applied", {"recovered": True})
                outcomes.append({"apply_id": journal["apply_id"], "outcome": "applied"})
            else:
                for change in journal["operations"]:
                    destination = root / change["path"]
                    for leftover in destination.parent.glob(f".{destination.name}.forge-stage-{journal['apply_id']}"):
                        leftover.unlink()
                    if change["path"] not in journal["done"]:
                        continue
                    if change["base_digest"] is None:
                        destination.unlink(missing_ok=True)
                    else:
                        temp = destination.with_name(f".{destination.name}.forge-restore")
                        with open(temp, "wb") as handle:
                            for chunk in self.controller.blobs.iter_verified(change["base_digest"]):
                                handle.write(chunk)
                        os.replace(temp, destination)
                self._record(journal["proposal_id"], journal["apply_id"], "rolled_back",
                             {"restored": journal["done"]})
                outcomes.append({"apply_id": journal["apply_id"], "outcome": "rolled_back"})
            journal_path.unlink()
        return outcomes
