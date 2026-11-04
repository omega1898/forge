"""Capture the exact bytes of a source tree, and recreate them safely.

capture_tree() walks an approved source root and copies every admitted file
into the blob store, producing a Manifest. The manifest's digest is the
snapshot's identity: any byte change, added or removed file, or executable
bit flip produces a different digest.

The guarantee is "these recorded bytes are what later jobs use", not "the
directory was copied at one perfect instant": an editor or build tool may
write while we read. A rescan detects that and retries a bounded number of
times before reporting workspace_changed.

materialize() recreates a manifest into a new, empty, controller-owned
directory, refusing paths that would escape it or collide on the target OS.
Jobs run against that copy, never against the user's live files.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from forge.artifacts.blobs import BlobStore
from forge.contracts.base import StrictModel
from forge.contracts.digests import Digest, canonical_json_bytes, sha256_digest
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.paths import check_relative_path, collision_key, portability_problems

ExcludeReason = Literal[
    "excluded_by_policy",
    "secret_by_policy",
    "symlink_denied",
    "special_file",
    "too_large",
    "undecodable_name",
    "unreadable",
    "invalid_name",
]


@dataclass(frozen=True)
class CapturePolicy:
    """What a capture admits. Versioned: the revision is part of the manifest."""

    revision: int = 1
    excluded_dirs: frozenset[str] = frozenset(
        {".git", ".forge", "__pycache__", ".venv", "venv", "node_modules",
         ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "build", "dist"}
    )
    excluded_files: tuple[str, ...] = ("*.pyc", "*.pyo", ".DS_Store")
    # Secrets are excluded by name *before* reading them. A content scanner
    # would be incomplete; not reading the file at all is the safer default.
    secret_files: tuple[str, ...] = (
        ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_rsa.*",
        "id_dsa*", "id_ecdsa*", "id_ed25519*", ".netrc", ".pypirc", ".npmrc",
        "credentials", "credentials.json", "*.kdbx",
    )
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    max_files: int = 20_000
    rescan_attempts: int = 3


class ManifestEntry(StrictModel):
    path: str
    digest: Digest
    size: int = Field(ge=0)
    executable: bool

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return check_relative_path(value)


class ExcludedEntry(StrictModel):
    path: str
    reason: ExcludeReason


class Manifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    capture_policy_revision: int
    base_commit: str | None
    entries: list[ManifestEntry]
    excluded: list[ExcludedEntry]

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        return sha256_digest(self.canonical_bytes())

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)


@dataclass
class CaptureResult:
    manifest: Manifest
    manifest_digest: str
    attempts: int
    warnings: list[str] = field(default_factory=list)


def capture_tree(root: Path, blobs: BlobStore, policy: CapturePolicy | None = None) -> CaptureResult:
    policy = policy or CapturePolicy()
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ForgeError(ErrorCode.INVALID_REQUEST, "source root is not a real directory")
    last_problem = ""
    for attempt in range(1, policy.rescan_attempts + 1):
        entries, excluded, observed = _walk_and_store(root, blobs, policy)
        changed = _changed_since(root, observed)
        if not changed:
            manifest = Manifest(
                capture_policy_revision=policy.revision,
                base_commit=read_git_head(root),
                entries=entries,
                excluded=excluded,
            )
            return CaptureResult(manifest=manifest, manifest_digest=manifest.digest(), attempts=attempt)
        last_problem = f"{len(changed)} file(s) changed during capture, e.g. {changed[0]}"
    raise ForgeError(
        ErrorCode.WORKSPACE_CHANGED,
        f"the source tree kept changing while being captured ({last_problem}); "
        "pause the writer (build, editor auto-save) and capture again",
    )


def _walk_and_store(
    root: Path, blobs: BlobStore, policy: CapturePolicy
) -> tuple[list[ManifestEntry], list[ExcludedEntry], dict[str, tuple[int, int, str]]]:
    entries: list[ManifestEntry] = []
    excluded: list[ExcludedEntry] = []
    observed: dict[str, tuple[int, int, str]] = {}
    total = 0
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            rel = f"{prefix}{child.name}"
            try:
                rel.encode("utf-8")
            except UnicodeEncodeError:
                excluded.append(
                    ExcludedEntry(path=rel.encode("utf-8", "replace").decode(), reason="undecodable_name")
                )
                continue
            try:
                check_relative_path(rel)
            except ValueError:
                excluded.append(ExcludedEntry(path=rel, reason="invalid_name"))
                continue
            info = child.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                excluded.append(ExcludedEntry(path=rel, reason="symlink_denied"))
            elif stat.S_ISDIR(info.st_mode):
                if child.name in policy.excluded_dirs:
                    excluded.append(ExcludedEntry(path=rel, reason="excluded_by_policy"))
                else:
                    stack.append((Path(child.path), rel + "/"))
            elif not stat.S_ISREG(info.st_mode):
                excluded.append(ExcludedEntry(path=rel, reason="special_file"))
            elif _matches(child.name, policy.secret_files):
                excluded.append(ExcludedEntry(path=rel, reason="secret_by_policy"))
            elif _matches(child.name, policy.excluded_files):
                excluded.append(ExcludedEntry(path=rel, reason="excluded_by_policy"))
            elif info.st_size > policy.max_file_bytes:
                excluded.append(ExcludedEntry(path=rel, reason="too_large"))
            else:
                if len(entries) >= policy.max_files:
                    raise ForgeError(
                        ErrorCode.RESOURCE_UNAVAILABLE,
                        f"source tree has more than {policy.max_files} admitted files",
                    )
                try:
                    stored = _store_file(Path(child.path), blobs, policy.max_file_bytes)
                except PermissionError:
                    excluded.append(ExcludedEntry(path=rel, reason="unreadable"))
                    continue
                total += stored.size
                if total > policy.max_total_bytes:
                    raise ForgeError(
                        ErrorCode.RESOURCE_UNAVAILABLE,
                        f"source tree exceeds the {policy.max_total_bytes}-byte capture limit",
                    )
                executable = bool(info.st_mode & stat.S_IXUSR) and os.name != "nt"
                entries.append(
                    ManifestEntry(path=rel, digest=stored.digest, size=stored.size, executable=executable)
                )
                observed[rel] = (info.st_size, info.st_mtime_ns, stored.digest)
    entries.sort(key=lambda entry: entry.path)
    excluded.sort(key=lambda entry: entry.path)
    return entries, excluded, observed


def _store_file(path: Path, blobs: BlobStore, max_bytes: int):  # noqa: ANN202 - BlobInfo
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise PermissionError("not a regular file at open time")
        return blobs.put_stream(handle, max_bytes=max_bytes)


def _changed_since(root: Path, observed: dict[str, tuple[int, int, str]]) -> list[str]:
    """Paths whose size or modification time moved after we read them."""
    changed = []
    for rel, (size, mtime_ns, _digest) in observed.items():
        try:
            info = os.stat(root / rel, follow_symlinks=False)
        except FileNotFoundError:
            changed.append(rel)
            continue
        if info.st_size != size or info.st_mtime_ns != mtime_ns:
            changed.append(rel)
    return sorted(changed)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def read_git_head(root: Path) -> str | None:
    """The commit HEAD points to, read from files without running git.

    Running `git` on an untrusted repository can execute commands through its
    configuration (for example fsmonitor hooks), so FORGE parses the few
    plain-text files it needs and returns None for anything unusual.
    """
    git_dir = root / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii", errors="strict").strip()
    except (OSError, UnicodeError):
        return None
    if _is_sha(head):
        return head
    if not head.startswith("ref: "):
        return None
    ref = head[5:].strip()
    try:
        check_relative_path(ref)
    except ValueError:
        return None
    try:
        value = (git_dir / ref).read_text(encoding="ascii").strip()
        return value if _is_sha(value) else None
    except (OSError, UnicodeError):
        pass
    try:
        for line in (git_dir / "packed-refs").read_text(encoding="ascii").splitlines():
            parts = line.split(" ")
            if len(parts) == 2 and parts[1] == ref and _is_sha(parts[0]):
                return parts[0]
    except (OSError, UnicodeError):
        return None
    return None


def _is_sha(text: str) -> bool:
    return len(text) in (40, 64) and all(char in "0123456789abcdef" for char in text)


# ----------------------------------------------------------- materialize --


def check_target(manifest: Manifest, target: str) -> list[str]:
    """Problems that block materializing this manifest on a target OS."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for entry in manifest.entries:
        problems.extend(f"{entry.path}: {issue}" for issue in portability_problems(entry.path, target))
        key = collision_key(entry.path, target)
        if key in seen:
            problems.append(f"{entry.path} and {seen[key]} collide on {target}")
        seen[key] = entry.path
    return problems


def materialize(manifest: Manifest, blobs: BlobStore, destination: Path, *, target: str = "linux") -> None:
    """Recreate the manifest's files inside a new, empty directory."""
    problems = check_target(manifest, target)
    if problems:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"snapshot cannot be materialized on {target}: {problems[0]}",
            details={"problems": problems[:20]},
        )
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for entry in manifest.entries:
        check_relative_path(entry.path)
        target_path = destination.joinpath(*entry.path.split("/"))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # The destination is fresh and only this function writes into it,
        # so no link can exist; O_EXCL|O_NOFOLLOW still refuses to follow one.
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(target_path, flags, 0o755 if entry.executable else 0o644)
        with os.fdopen(fd, "wb") as out:
            for chunk in blobs.iter_verified(entry.digest, expected_size=entry.size):
                out.write(chunk)


def load_manifest(blobs: BlobStore, digest: str) -> Manifest:
    data = blobs.read_bytes(digest, max_bytes=256 * 1024 * 1024)
    manifest = Manifest.model_validate_json(data)
    if manifest.digest() != digest:
        raise ForgeError(
            ErrorCode.ARTIFACT_MISSING, "manifest bytes are not in canonical form for their digest"
        )
    return manifest
