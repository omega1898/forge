"""Content-addressed blob storage.

Bytes are stored under a path derived from their SHA-256 digest:
blobs/sha256/ab/cdef... Callers never pass filesystem paths; they pass
digests (and, at the controller level, artifact IDs checked against the
project). Writing goes to a temporary file, is flushed to disk, and only then
renamed into place, so a crash leaves at worst an orphan temporary file,
never a half-written blob under a valid name.

"Immutable" means FORGE never rewrites a blob. Someone with direct disk
access could still alter one, so every read re-verifies the digest.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from forge.contracts.digests import validate_digest
from forge.contracts.errors import ErrorCode, ForgeError

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class BlobInfo:
    digest: str
    size: int


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._objects = self.root / "sha256"
        self._tmp = self.root / "tmp"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._tmp.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        hexdigest = validate_digest(digest).removeprefix("sha256:")
        return self._objects / hexdigest[:2] / hexdigest[2:]

    def exists(self, digest: str) -> bool:
        return self._path(digest).is_file()

    def put_stream(self, source: BinaryIO, *, max_bytes: int) -> BlobInfo:
        """Copy a stream into the store, refusing more than max_bytes."""
        hasher = hashlib.sha256()
        size = 0
        fd, temp_name = tempfile.mkstemp(dir=self._tmp, prefix="incoming-")
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := source.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ForgeError(
                            ErrorCode.RESOURCE_UNAVAILABLE,
                            f"artifact exceeds the {max_bytes}-byte limit",
                        )
                    hasher.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            digest = "sha256:" + hasher.hexdigest()
            final = self._path(digest)
            if final.exists():
                os.unlink(temp_name)  # identical bytes already stored
            else:
                final.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(temp_name, 0o444)
                os.replace(temp_name, final)
                _fsync_dir(final.parent)
            return BlobInfo(digest=digest, size=size)
        except BaseException:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise

    def put_bytes(self, data: bytes, *, max_bytes: int | None = None) -> BlobInfo:
        import io

        return self.put_stream(io.BytesIO(data), max_bytes=max_bytes or len(data))

    def iter_verified(self, digest: str, *, expected_size: int | None = None) -> Iterator[bytes]:
        """Yield the blob's bytes; raise if they no longer match the digest.

        The final check happens after the last chunk, so callers must not act
        on the data until iteration completes (write to a temp file first).
        """
        path = self._path(digest)
        try:
            handle = open(path, "rb")  # noqa: SIM115 - closed in finally
        except FileNotFoundError:
            raise ForgeError(
                ErrorCode.ARTIFACT_MISSING, f"blob {digest} is not in the store"
            ) from None
        hasher = hashlib.sha256()
        size = 0
        try:
            while chunk := handle.read(CHUNK):
                hasher.update(chunk)
                size += len(chunk)
                yield chunk
        finally:
            handle.close()
        if "sha256:" + hasher.hexdigest() != digest or (
            expected_size is not None and size != expected_size
        ):
            raise ForgeError(
                ErrorCode.ARTIFACT_MISSING,
                f"blob {digest} failed its integrity check; the stored bytes were altered",
                details={"reason": "integrity"},
            )

    def read_bytes(self, digest: str, *, max_bytes: int) -> bytes:
        data = bytearray()
        for chunk in self.iter_verified(digest):
            data += chunk
            if len(data) > max_bytes:
                raise ForgeError(
                    ErrorCode.RESOURCE_UNAVAILABLE, f"blob {digest} is larger than {max_bytes} bytes"
                )
        return bytes(data)

    def verify(self, digest: str) -> bool:
        try:
            for _ in self.iter_verified(digest):
                pass
        except ForgeError:
            return False
        return True

    def delete(self, digest: str) -> None:
        path = self._path(digest)
        if path.exists():
            os.chmod(path, 0o644)
            path.unlink()


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":  # directories cannot be opened for fsync on Windows
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
