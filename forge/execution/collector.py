"""Import a finished job's output directory into the artifact store.

The output directory was written by untrusted code. The collector:

1. lists everything without following links, and checks every entry first:
   regular files only (no symlinks, hard links, devices, sockets, FIFOs),
   valid relative names, bounded depth, bounded count and total size, and no
   names that would collide on Windows or macOS;
2. only if *every* entry passes, copies the files into the blob store,
   re-checking each file at open time (O_NOFOLLOW + fstat) in case the tree
   changed between listing and reading.

All-or-nothing is deliberate: a test runner that suddenly emits a symlink to
/etc is suspicious, and importing the "good" half would hide that.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import Field

from forge.artifacts.blobs import BlobStore
from forge.contracts.base import StrictModel
from forge.contracts.digests import Digest
from forge.contracts.paths import check_relative_path, collision_key

RejectReason = Literal[
    "symlink", "hard_link", "special_file", "invalid_name", "too_deep",
    "too_many_files", "too_large", "name_collision", "changed_during_collection",
]


class CollectedFile(StrictModel):
    path: str
    digest: Digest
    size: int = Field(ge=0)


class RejectedEntry(StrictModel):
    path: str
    reason: RejectReason


class CollectionResult(StrictModel):
    accepted: bool
    files: list[CollectedFile]
    rejected: list[RejectedEntry]
    total_bytes: int


def collect_outputs(
    output_dir: Path, blobs: BlobStore, *, max_files: int, max_bytes: int, max_depth: int = 16
) -> CollectionResult:
    output_dir = Path(output_dir)
    planned: list[tuple[str, int, int]] = []  # path, size, inode
    rejected: list[RejectedEntry] = []
    total = 0
    keys: dict[str, str] = {}
    stack: list[tuple[Path, str, int]] = [(output_dir, "", 0)]
    while stack:
        directory, prefix, depth = stack.pop()
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
        for entry in entries:
            rel = prefix + entry.name
            try:
                check_relative_path(rel)
                rel.encode("utf-8")
            except (ValueError, UnicodeEncodeError):
                rejected.append(RejectedEntry(path=rel.encode("utf-8", "replace").decode(), reason="invalid_name"))
                continue
            # os.lstat, not DirEntry.stat: on Windows the latter reports inode and
            # link count as 0, which would defeat the hard-link and swap checks.
            info = os.lstat(entry.path)
            if stat.S_ISLNK(info.st_mode):
                rejected.append(RejectedEntry(path=rel, reason="symlink"))
            elif stat.S_ISDIR(info.st_mode):
                if depth + 1 > max_depth:
                    rejected.append(RejectedEntry(path=rel, reason="too_deep"))
                else:
                    stack.append((Path(entry.path), rel + "/", depth + 1))
            elif not stat.S_ISREG(info.st_mode):
                rejected.append(RejectedEntry(path=rel, reason="special_file"))
            elif info.st_nlink > 1:
                rejected.append(RejectedEntry(path=rel, reason="hard_link"))
            else:
                for target in ("windows", "macos"):
                    key = target + ":" + collision_key(rel, target)
                    if key in keys:
                        rejected.append(RejectedEntry(path=rel, reason="name_collision"))
                        break
                    keys[key] = rel
                else:
                    planned.append((rel, info.st_size, info.st_ino))
                    total += info.st_size
    if len(planned) > max_files:
        rejected.append(RejectedEntry(path=".", reason="too_many_files"))
    if total > max_bytes:
        rejected.append(RejectedEntry(path=".", reason="too_large"))
    if rejected:
        return CollectionResult(accepted=False, files=[], rejected=rejected, total_bytes=total)

    files: list[CollectedFile] = []
    for rel, size, inode in sorted(planned):
        path = output_dir.joinpath(*rel.split("/"))
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return CollectionResult(
                accepted=False, files=[], rejected=[RejectedEntry(path=rel, reason="changed_during_collection")],
                total_bytes=total,
            )
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_ino != inode or info.st_size != size:
                return CollectionResult(
                    accepted=False, files=[],
                    rejected=[RejectedEntry(path=rel, reason="changed_during_collection")],
                    total_bytes=total,
                )
            stored = blobs.put_stream(handle, max_bytes=size)
        files.append(CollectedFile(path=rel, digest=stored.digest, size=stored.size))
    return CollectionResult(accepted=True, files=files, rejected=[], total_bytes=total)
