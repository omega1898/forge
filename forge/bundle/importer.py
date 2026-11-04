"""Offline bundles: signed, verified, versioned, atomically promoted (ticket F24).

The FORGE runtime never downloads anything. An online staging machine
builds a bundle (models, tools, wheels, datasets, fixtures) and signs its
manifest; the offline machine imports it:

1. copy into quarantine (archives: no links, no traversal, bounded size and
   file count);
2. verify the Ed25519 signature with a key enrolled beforehand through a
   separate trusted step (a signature proves nothing if the attacker
   supplies both the bundle and the key);
3. check freshness against trusted local time, and the version against the
   ledger of the highest version seen (no silent downgrade or freeze);
4. verify every file's digest and size; check platform compatibility;
5. install into a new immutable version directory and run admission checks;
6. atomically switch the active-version pointer, keeping the previous
   version for rollback.

Rollback is a deliberate, recorded exception made by a trusted human; it
never erases the highest-seen version. Signatures use the `cryptography`
package (an established implementation; FORGE writes no cryptography).
A signed bundle is not thereby malware-free; this module proves origin and
integrity only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.clock import Clock, SystemClock, Timestamp, format_timestamp, parse_timestamp
from forge.contracts.commands import ActorContext
from forge.contracts.digests import Digest, canonical_json_bytes
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.paths import check_relative_path

MAX_BUNDLE_BYTES = 8 * 1024 * 1024 * 1024
MAX_BUNDLE_FILES = 10_000

RejectReason = Literal[
    "unsigned", "unknown_key", "bad_signature", "expired", "downgrade", "replay", "corrupted",
    "incompatible_platform", "unsafe_archive", "malformed", "admission_failed",
]


class BundleFile(StrictModel):
    path: str
    digest: Digest
    size: int = Field(ge=0)
    kind: Literal["model", "tool", "wheel", "dataset", "fixture", "docs", "extension"]
    license: str = Field(max_length=200)


class BundleManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    sequence: int = Field(ge=1)              # strictly increasing release number
    created_at: Timestamp
    expires_at: Timestamp
    platforms: list[str] = Field(min_length=1)   # e.g. ["linux-x86_64"], or ["any"]
    files: list[BundleFile] = Field(min_length=1, max_length=MAX_BUNDLE_FILES)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class BundleRejected(ForgeError):
    def __init__(self, reason: RejectReason, message: str) -> None:
        super().__init__(ErrorCode.INVALID_REQUEST, message, details={"reason": reason})
        self.reason = reason


def host_platform() -> str:
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("arm64", "aarch64")
    return f"{platform.system().lower()}-{machine}"


# --------------------------------------------------------------- staging side


def sign_manifest(manifest: BundleManifest, key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.sign(manifest.canonical_bytes())).decode()


def build_bundle(source_dir: Path, out_dir: Path, *, bundle_id: str, sequence: int, created_at: str,
                 expires_at: str, platforms: list[str], kinds: dict[str, str], key: Ed25519PrivateKey | None) -> Path:
    """Staging helper: write manifest (+ signature) next to a copy of the files."""
    source_dir, out_dir = Path(source_dir), Path(out_dir)
    shutil.copytree(source_dir, out_dir / "files")
    entries = []
    for path in sorted(p for p in (out_dir / "files").rglob("*") if p.is_file()):
        relative = path.relative_to(out_dir / "files").as_posix()
        data = path.read_bytes()
        entries.append(BundleFile(path=relative, digest="sha256:" + hashlib.sha256(data).hexdigest(), size=len(data),
                                  kind=kinds.get(relative, "fixture"), license="see bundle notice"))
    manifest = BundleManifest(bundle_id=bundle_id, sequence=sequence, created_at=created_at, expires_at=expires_at,
                              platforms=platforms, files=entries)
    (out_dir / "manifest.json").write_bytes(manifest.canonical_bytes())
    if key is not None:
        (out_dir / "manifest.sig").write_text(sign_manifest(manifest, key))
    return out_dir


# ---------------------------------------------------------- offline importer


class BundleImporter:
    def __init__(self, install_root: Path, *, clock: Clock | None = None) -> None:
        self.root = Path(install_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self._db = sqlite3.connect(str(self.root / "ledger.db"), isolation_level=None)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS keys (key_id TEXT PRIMARY KEY, public_key TEXT NOT NULL,"
            " enrolled_by TEXT NOT NULL, enrolled_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS versions (bundle_id TEXT, sequence INTEGER, manifest_digest TEXT,"
            " installed_at TEXT, PRIMARY KEY (bundle_id, sequence));"
            "CREATE TABLE IF NOT EXISTS highest (bundle_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS events (at TEXT, bundle_id TEXT, kind TEXT, detail TEXT);"
        )

    # -- trust root ---------------------------------------------------------

    def enroll_key(self, key_id: str, public_key: Ed25519PublicKey, actor: ActorContext) -> None:
        """Separate, trusted enrollment step: only a human on the trusted channel."""
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "bundle signing keys are enrolled only by the trusted user")
        raw = public_key.public_bytes_raw()
        self._db.execute("INSERT OR REPLACE INTO keys VALUES (?, ?, ?, ?, 0)",
                         (key_id, base64.b64encode(raw).decode(), actor.actor_id, format_timestamp(self.clock.now())))

    def revoke_key(self, key_id: str, actor: ActorContext) -> None:
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only the trusted user revokes signing keys")
        self._db.execute("UPDATE keys SET revoked = 1 WHERE key_id = ?", (key_id,))

    def _keys(self) -> list[tuple[str, Ed25519PublicKey]]:
        rows = self._db.execute("SELECT key_id, public_key FROM keys WHERE revoked = 0 ORDER BY key_id").fetchall()
        return [(row[0], Ed25519PublicKey.from_public_bytes(base64.b64decode(row[1]))) for row in rows]

    # -- import ---------------------------------------------------------------

    def active(self, bundle_id: str) -> int | None:
        pointer = self.root / bundle_id / "active"
        return int(pointer.read_text()) if pointer.exists() else None

    def highest_seen(self, bundle_id: str) -> int:
        row = self._db.execute("SELECT sequence FROM highest WHERE bundle_id = ?", (bundle_id,)).fetchone()
        return row[0] if row else 0

    def import_bundle(self, source: Path, *, admission: Callable[[Path], bool] | None = None) -> int:
        quarantine = self.root / "quarantine" / f"q{os.getpid()}-{self.clock.now().timestamp():.0f}"
        shutil.rmtree(quarantine, ignore_errors=True)
        try:
            self._copy_into_quarantine(Path(source), quarantine)
            manifest, key_id = self._verify_manifest(quarantine)
            self._verify_files(manifest, quarantine)
            version_dir = self.root / manifest.bundle_id / "versions" / str(manifest.sequence)
            if version_dir.exists():
                raise BundleRejected("replay", f"version {manifest.sequence} is already installed")
            version_dir.parent.mkdir(parents=True, exist_ok=True)
            staging = version_dir.with_name(f".{manifest.sequence}.staging")
            shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(quarantine / "files", staging)
            (staging / "manifest.json").write_bytes(manifest.canonical_bytes())
            if admission is not None and not admission(staging):
                shutil.rmtree(staging)
                raise BundleRejected("admission_failed", "the bundle failed its admission checks")
            for path in staging.rglob("*"):
                if path.is_file():
                    os.chmod(path, 0o444)  # immutable once installed
            os.replace(staging, version_dir)
            # Ledger first, pointer second: a crash in between leaves the old
            # version active and the highest-seen mark raised, which is safe.
            digest = "sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest()
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute("INSERT INTO versions VALUES (?, ?, ?, ?)",
                                 (manifest.bundle_id, manifest.sequence, digest, format_timestamp(self.clock.now())))
                self._db.execute("INSERT INTO highest VALUES (?, ?) ON CONFLICT (bundle_id) DO UPDATE SET"
                                 " sequence = max(sequence, excluded.sequence)", (manifest.bundle_id, manifest.sequence))
                self._log(manifest.bundle_id, "installed", json.dumps({"sequence": manifest.sequence, "key": key_id}))
            self._switch_active(manifest.bundle_id, manifest.sequence)
            return manifest.sequence
        finally:
            shutil.rmtree(quarantine, ignore_errors=True)

    def _copy_into_quarantine(self, source: Path, quarantine: Path) -> None:
        quarantine.mkdir(parents=True)
        if source.is_file() and tarfile.is_tarfile(source):
            self._safe_extract(source, quarantine)
            return
        if not source.is_dir():
            raise BundleRejected("malformed", "a bundle is a directory or a tar archive")
        total = 0
        for path in source.rglob("*"):
            if path.is_symlink():
                raise BundleRejected("unsafe_archive", f"bundle contains a link: {path.name}")
            if path.is_file():
                total += path.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise BundleRejected("unsafe_archive", "bundle exceeds the size limit")
        shutil.copytree(source, quarantine, dirs_exist_ok=True, symlinks=True)

    def _safe_extract(self, archive: Path, quarantine: Path) -> None:
        total, count = 0, 0
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            for member in members:
                count += 1
                if count > MAX_BUNDLE_FILES + 10:
                    raise BundleRejected("unsafe_archive", "archive has too many entries")
                if not (member.isfile() or member.isdir()):
                    raise BundleRejected("unsafe_archive", f"archive entry {member.name!r} is a link or device")
                try:
                    check_relative_path(member.name.rstrip("/"))
                except ValueError:
                    raise BundleRejected("unsafe_archive", f"archive entry {member.name!r} escapes the bundle") from None
                total += member.size
                if total > MAX_BUNDLE_BYTES:
                    raise BundleRejected("unsafe_archive", "archive expands beyond the size limit")
            tar.extractall(quarantine, members=members, filter="data")

    def _verify_manifest(self, quarantine: Path) -> tuple[BundleManifest, str]:
        manifest_path, signature_path = quarantine / "manifest.json", quarantine / "manifest.sig"
        if not manifest_path.exists():
            raise BundleRejected("malformed", "bundle has no manifest.json")
        if not signature_path.exists():
            raise BundleRejected("unsigned", "bundle manifest is not signed")
        data = manifest_path.read_bytes()
        try:
            signature = base64.b64decode(signature_path.read_text().strip(), validate=True)
        except ValueError:
            raise BundleRejected("bad_signature", "signature is not valid base64") from None
        keys = self._keys()
        if not keys:
            raise BundleRejected("unknown_key", "no signing key is enrolled on this machine")
        for key_id, key in keys:
            try:
                key.verify(signature, data)
                break
            except InvalidSignature:
                continue
        else:
            raise BundleRejected("bad_signature", "the manifest signature does not verify with any enrolled key")
        try:
            manifest = BundleManifest.model_validate_json(data)
        except ValueError as error:
            raise BundleRejected("malformed", f"manifest is invalid: {str(error)[:200]}") from None
        if manifest.canonical_bytes() != data:
            raise BundleRejected("malformed", "manifest is not in canonical form")
        now = self.clock.now()
        if parse_timestamp(manifest.expires_at) <= now:
            raise BundleRejected("expired", f"bundle expired at {manifest.expires_at}")
        highest = self.highest_seen(manifest.bundle_id)
        if manifest.sequence < highest:
            raise BundleRejected("downgrade", f"version {manifest.sequence} is older than {highest}, already seen")
        if "any" not in manifest.platforms and host_platform() not in manifest.platforms:
            raise BundleRejected("incompatible_platform", f"bundle supports {manifest.platforms}, not {host_platform()}")
        return manifest, key_id

    def _verify_files(self, manifest: BundleManifest, quarantine: Path) -> None:
        listed = set()
        for entry in manifest.files:
            try:
                check_relative_path(entry.path)
            except ValueError:
                raise BundleRejected("malformed", f"manifest lists an unsafe path {entry.path[:80]!r}") from None
            if entry.path in listed:
                raise BundleRejected("malformed", f"manifest lists {entry.path} twice")
            listed.add(entry.path)
            path = quarantine / "files" / entry.path
            if not path.is_file() or path.is_symlink():
                raise BundleRejected("corrupted", f"{entry.path} is missing")
            hasher = hashlib.sha256()
            size = 0
            with open(path, "rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    hasher.update(chunk)
                    size += len(chunk)
            if size != entry.size or "sha256:" + hasher.hexdigest() != entry.digest:
                raise BundleRejected("corrupted", f"{entry.path} does not match its recorded digest")
        extra = [p.relative_to(quarantine / "files").as_posix() for p in (quarantine / "files").rglob("*")
                 if p.is_file() and p.relative_to(quarantine / "files").as_posix() not in listed]
        if extra:
            raise BundleRejected("corrupted", f"bundle carries files its manifest does not list: {extra[:3]}")

    def _switch_active(self, bundle_id: str, sequence: int) -> None:
        pointer = self.root / bundle_id / "active"
        temp = pointer.with_suffix(".tmp")
        temp.write_text(str(sequence))
        os.replace(temp, pointer)  # one atomic step: old version or new, never half

    def rollback(self, bundle_id: str, to_sequence: int, actor: ActorContext, reason: str) -> None:
        """Deliberate operational exception; the highest-seen version is never lowered."""
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only the trusted user rolls back a bundle")
        if not (self.root / bundle_id / "versions" / str(to_sequence)).is_dir():
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"version {to_sequence} is not installed")
        self._switch_active(bundle_id, to_sequence)
        self._log(bundle_id, "rollback", json.dumps({"to": to_sequence, "by": actor.actor_id, "reason": reason}))

    def history(self, bundle_id: str) -> list[tuple[str, str]]:
        rows = self._db.execute("SELECT kind, detail FROM events WHERE bundle_id = ? ORDER BY rowid", (bundle_id,))
        return [(row[0], row[1]) for row in rows]

    def _log(self, bundle_id: str, kind: str, detail: str) -> None:
        self._db.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                         (format_timestamp(self.clock.now()), bundle_id, kind, detail))
