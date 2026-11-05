"""Trusted grants: what FORGE may do on a project without asking again.

Security rule (blueprint decision 8): authority is kept outside untrusted
repositories. A grants file inside a repository is only a *proposal*. Turning
a proposal into an approved grant requires a human actor on the trusted local
channel. Neither a model, an execution host, nor a repository file can do it.

Threat this addresses: a hostile repository (or a model that read one)
shipping a file that says "FORGE may run anything, approved". Under the
assumption that the trust database's file permissions are intact and the
actor context is established by the controller (not read from input), such a
file cannot create authority.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from forge.contracts.base import StrictModel, Text
from forge.contracts.clock import Clock, SystemClock, Timestamp, format_timestamp, parse_timestamp
from forge.contracts.commands import ActorContext
from forge.contracts.digests import Digest, canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError, safe_preview
from forge.contracts.ids import ActorId, GrantId, ProjectId, new_id
from forge.record.db import open_connection, write_transaction
from forge.record.lock import ProjectLock
from forge.record.migrate import migrate

MAX_GRANT_LIFETIME = timedelta(days=90)
MAX_REPOSITORY_GRANT_FILE_BYTES = 64 * 1024

GrantAction = Literal["read_source", "run_registered_tool", "apply_change", "record_review"]


def _normalized_relative_path(value: str) -> str:
    """Accept only clean, relative, forward-slash paths inside the project.

    Rejects absolute paths, drive letters, '..' traversal, backslashes and NUL,
    because each can make a scope point outside the project.
    """
    if not value or "\x00" in value or "\\" in value:
        raise ValueError(f"invalid path scope {safe_preview(value)}")
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        raise ValueError(f"path scope must be relative: {safe_preview(value)}")
    parts = PurePosixPath(value).parts
    if any(part in ("..", ".") for part in parts) or str(PurePosixPath(*parts)) != value:
        raise ValueError(f"path scope must be normalized without '.' or '..': {safe_preview(value)}")
    return value


class GrantProposal(StrictModel):
    """A request for authority. Has no effect until approved."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: ProjectId
    actions: list[GrantAction] = Field(min_length=1, max_length=8)
    path_scope: list[str] = Field(min_length=1, max_length=64)
    justification: Text(2000)
    expires_at: Timestamp
    origin: Literal["user", "repository", "model"] = "user"

    @field_validator("path_scope")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        return [_normalized_relative_path(item) for item in value]

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


class Grant(StrictModel):
    grant_id: GrantId
    project_id: ProjectId
    actions: list[GrantAction]
    path_scope: list[str]
    justification: str
    proposal_origin: Literal["user", "repository", "model"]
    proposal_digest: Digest
    status: Literal["approved", "revoked"]
    approved_by: ActorId
    approved_at: Timestamp
    expires_at: Timestamp
    revoked_at: Timestamp | None
    revoked_by: ActorId | None
    record_version: int


class _RepositoryGrantEntry(StrictModel):
    """Exactly the fields a repository file may propose. No status, no approver."""

    actions: list[GrantAction] = Field(min_length=1, max_length=8)
    path_scope: list[str] = Field(min_length=1, max_length=64)
    justification: Text(2000)
    expires_at: Timestamp


class _RepositoryGrantFile(StrictModel):
    schema_version: Literal["1.0"]
    proposals: list[_RepositoryGrantEntry] = Field(max_length=32)


def load_repository_grant_proposals(file_path: Path, project_id: str) -> list[GrantProposal]:
    """Read a repository's grants file as proposals only.

    Any attempt to smuggle approval fields ("status", "approved_by", ...) is
    rejected as an invalid file rather than silently ignored, so the user sees
    that the repository tried.
    """
    file_path = Path(file_path)
    if file_path.is_symlink():
        raise ForgeError(ErrorCode.INVALID_REQUEST, "repository grants file must not be a symlink")
    data = file_path.read_bytes()
    if len(data) > MAX_REPOSITORY_GRANT_FILE_BYTES:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "repository grants file is too large")
    try:
        parsed = _RepositoryGrantFile.model_validate_json(data)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"repository grants file rejected at {location}: {first['msg']}",
        ) from None
    return [
        GrantProposal(
            project_id=project_id,
            actions=entry.actions,
            path_scope=list(entry.path_scope),
            justification=entry.justification,
            expires_at=entry.expires_at,
            origin="repository",
        )
        for entry in parsed.proposals
    ]


class TrustStore:
    """The user-level database of approved grants."""

    def __init__(self, conn: sqlite3.Connection, lock: ProjectLock, clock: Clock) -> None:
        self.conn = conn
        self._lock = lock
        self._clock = clock

    @classmethod
    def open(cls, path: Path, clock: Clock | None = None) -> TrustStore:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        clock = clock or SystemClock()
        lock = ProjectLock(path.with_name(path.name + ".lock"))
        lock.acquire()
        conn: sqlite3.Connection | None = None
        try:
            conn = open_connection(path)
            migrate(conn, clock, package="forge.authority")
        except BaseException:
            if conn is not None:
                conn.close()
            lock.release()
            raise
        return cls(conn, lock, clock)

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self._lock.release()

    def __enter__(self) -> TrustStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def approve(self, proposal: GrantProposal, approver: ActorContext) -> Grant:
        if not approver.is_trusted_human:
            raise ForgeError(
                ErrorCode.PERMISSION_DENIED,
                "grants can only be approved by a human on the trusted local channel",
                details={"actor_kind": approver.kind, "channel": approver.channel},
            )
        now = self._clock.now()
        expires = parse_timestamp(proposal.expires_at)
        if expires <= now:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "grant expiry must be in the future")
        if expires - now > MAX_GRANT_LIFETIME:
            raise ForgeError(
                ErrorCode.INVALID_REQUEST,
                f"grant lifetime exceeds the maximum of {MAX_GRANT_LIFETIME.days} days",
            )
        grant_id = new_id("grant")
        with write_transaction(self.conn):
            self.conn.execute(
                "INSERT INTO grants (grant_id, project_id, actions_json, path_scope_json,"
                " justification, proposal_origin, proposal_digest, status, approved_by,"
                " approved_channel, approved_at, expires_at, record_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, 1)",
                (
                    grant_id,
                    proposal.project_id,
                    canonical_json_bytes(proposal.actions).decode(),
                    canonical_json_bytes(proposal.path_scope).decode(),
                    proposal.justification,
                    proposal.origin,
                    proposal.digest(),
                    approver.actor_id,
                    approver.channel,
                    format_timestamp(now),
                    proposal.expires_at,
                ),
            )
        grant = self.get(grant_id)
        assert grant is not None
        return grant

    def revoke(self, grant_id: str, actor: ActorContext) -> Grant:
        if not actor.is_trusted_human:
            raise ForgeError(
                ErrorCode.PERMISSION_DENIED,
                "grants can only be revoked by a human on the trusted local channel",
            )
        with write_transaction(self.conn):
            cursor = self.conn.execute(
                "UPDATE grants SET status = 'revoked', revoked_at = ?, revoked_by = ?,"
                " record_version = record_version + 1 WHERE grant_id = ? AND status = 'approved'",
                (format_timestamp(self._clock.now()), actor.actor_id, grant_id),
            )
            if cursor.rowcount != 1:
                raise ForgeError(
                    ErrorCode.INVALID_REQUEST, "grant not found or already revoked", related_ids=[grant_id]
                )
        grant = self.get(grant_id)
        assert grant is not None
        return grant

    def approve_source_root(
        self, project_id: str, label: str, host_path: Path, approver: ActorContext
    ) -> Path:
        """Bind a project's source-root label to a host directory (human only)."""
        if not approver.is_trusted_human:
            raise ForgeError(
                ErrorCode.PERMISSION_DENIED,
                "source roots can only be approved by a human on the trusted local channel",
            )
        candidate = Path(host_path)
        if not candidate.is_absolute():
            raise ForgeError(ErrorCode.INVALID_REQUEST, "source root must be an absolute path")
        if candidate.is_symlink() or not candidate.is_dir():
            raise ForgeError(ErrorCode.INVALID_REQUEST, "source root must be a real directory, not a link")
        resolved = candidate.resolve(strict=True)
        too_broad = {Path(resolved.anchor), Path.home().resolve()}
        if resolved in too_broad:
            raise ForgeError(
                ErrorCode.INVALID_REQUEST,
                "refusing a filesystem root or home directory as a source root; choose the project folder",
            )
        with write_transaction(self.conn):
            self.conn.execute(
                "INSERT INTO source_roots (project_id, label, host_path, approved_by, approved_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (project_id, label) DO UPDATE SET"
                " host_path = excluded.host_path, approved_by = excluded.approved_by,"
                " approved_at = excluded.approved_at",
                (project_id, label, str(resolved), approver.actor_id, format_timestamp(self._clock.now())),
            )
        return resolved

    def source_root(self, project_id: str, label: str) -> Path | None:
        row = self.conn.execute(
            "SELECT host_path FROM source_roots WHERE project_id = ? AND label = ?", (project_id, label)
        ).fetchone()
        return None if row is None else Path(row[0])

    def get(self, grant_id: str) -> Grant | None:
        row = self.conn.execute("SELECT * FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
        return None if row is None else _grant_from_row(row)

    def effective_grant(self, grant_id: str, project_id: str) -> Grant | None:
        """The grant if it is approved, unexpired, and for this project; else None."""
        grant = self.get(grant_id)
        if grant is None or grant.project_id != project_id or grant.status != "approved":
            return None
        if parse_timestamp(grant.expires_at) <= self._clock.now():
            return None
        return grant

    def effective_grants(self, project_id: str) -> list[Grant]:
        rows = self.conn.execute(
            "SELECT grant_id FROM grants WHERE project_id = ? AND status = 'approved' ORDER BY grant_id",
            (project_id,),
        )
        found = [self.effective_grant(row[0], project_id) for row in rows]
        return [grant for grant in found if grant is not None]


def _grant_from_row(row: sqlite3.Row) -> Grant:
    return Grant(
        grant_id=row["grant_id"],
        project_id=row["project_id"],
        actions=json.loads(row["actions_json"]),
        path_scope=json.loads(row["path_scope_json"]),
        justification=row["justification"],
        proposal_origin=row["proposal_origin"],
        proposal_digest=row["proposal_digest"],
        status=row["status"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        revoked_by=row["revoked_by"],
        record_version=row["record_version"],
    )
