"""Snapshot-bound source index: exact paths, Python definitions, full-text search.

Index records always name the snapshot they came from; a query against one
snapshot can never return text from another. Each snapshot's index is built
into a temporary database file and published by an atomic rename, so a
reader sees an old complete index or a new complete one, never half.

Python files are parsed with `ast` (parsing never executes code) to find
definitions, imports and tests with exact line ranges. Other text is chunked
by lines for SQLite FTS5 full-text search. Embeddings are intentionally
absent: the blueprint adds them only after a retrieval benchmark shows a
benefit.

Evaluation isolation: a file whose bytes equal a protected evaluator suite,
or that sits under a protected path, is quarantined, not indexed.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from forge.artifacts.blobs import BlobStore
from forge.contracts.digests import sha256_digest
from forge.workspace.snapshot import Manifest
from forge.workspace.visibility import INDEX_HIDDEN, path_key

CHUNK_LINES = 40
MAX_INDEXED_FILE = 1024 * 1024
TEXT_SUFFIXES = (".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml", ".rst", ".c", ".h",
                 ".cpp", ".hpp", ".cc", ".cxx", ".hh", ".sh", ".bash", ".sql", ".java", ".cbl", ".cob", ".cpy", ".ml",
                 ".mli", ".js", ".ts", ".adb", ".ads", ".ada", ".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08",
                 ".go", ".rs", ".cs", ".asm", ".s")
# Evaluator files, hidden tests and forge.toml (whose expected results would leak through search hits).
DEFAULT_PROTECTED = INDEX_HIDDEN


@dataclass(frozen=True)
class SourceRef:
    path: str
    start_line: int
    end_line: int
    kind: str          # definition, test, chunk, path
    name: str
    score: float
    reason: str


class SnapshotIndex:
    def __init__(self, index_dir: Path) -> None:
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, manifest_digest: str) -> Path:
        return self.index_dir / f"{manifest_digest.removeprefix('sha256:')}.db"

    def build(
        self, manifest: Manifest, manifest_digest: str, blobs: BlobStore, *,
        protected_digests: set[str] = frozenset(), protected_paths: tuple[str, ...] = DEFAULT_PROTECTED,
    ) -> dict[str, list[str]]:
        """Build (or reuse) the index for one snapshot. Returns quarantined paths by reason."""
        final = self._path(manifest_digest)
        quarantined: dict[str, list[str]] = {"evaluation_isolation": [], "not_text": [], "too_large": []}
        protection = json.dumps({"paths": list(protected_paths), "digests": sorted(protected_digests)})
        if final.exists():
            # Reuse only an index built with exactly these protections; otherwise rebuild, so a
            # build made with fewer exclusions can never serve a reader that needs more.
            db = self._connect(manifest_digest)
            try:
                row = db.execute("SELECT value FROM meta WHERE key = 'protection'").fetchone()
                if row is not None and row[0] == protection:
                    for item in db.execute("SELECT path, reason FROM quarantine ORDER BY path"):
                        quarantined[item["reason"]].append(item["path"])
                    return quarantined
            finally:
                db.close()
            final.unlink()
        temp = final.with_suffix(f".tmp{os.getpid()}")
        temp.unlink(missing_ok=True)
        db = sqlite3.connect(str(temp))
        try:
            db.executescript(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "CREATE TABLE files (path TEXT PRIMARY KEY, digest TEXT, lines INTEGER);"
                "CREATE TABLE symbols (name TEXT, kind TEXT, path TEXT, start_line INTEGER, end_line INTEGER,"
                " signature TEXT);"
                "CREATE TABLE imports (path TEXT, module TEXT);"
                "CREATE TABLE quarantine (path TEXT PRIMARY KEY, reason TEXT);"
                "CREATE VIRTUAL TABLE chunks USING fts5(path UNINDEXED, start_line UNINDEXED,"
                " end_line UNINDEXED, body);"
            )
            db.execute("INSERT INTO meta VALUES ('manifest_digest', ?)", (manifest_digest,))
            db.execute("INSERT INTO meta VALUES ('protection', ?)", (protection,))
            prefixes = tuple(path_key(prefix) for prefix in protected_paths)
            for entry in manifest.entries:
                if entry.digest in protected_digests or path_key(entry.path).startswith(prefixes):
                    quarantined["evaluation_isolation"].append(entry.path)
                    continue
                if not entry.path.endswith(TEXT_SUFFIXES):
                    quarantined["not_text"].append(entry.path)
                    continue
                if entry.size > MAX_INDEXED_FILE:
                    quarantined["too_large"].append(entry.path)
                    continue
                data = blobs.read_bytes(entry.digest, max_bytes=MAX_INDEXED_FILE)
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    quarantined["not_text"].append(entry.path)
                    continue
                lines = text.splitlines()
                db.execute("INSERT INTO files VALUES (?, ?, ?)", (entry.path, entry.digest, len(lines)))
                for start in range(0, max(len(lines), 1), CHUNK_LINES):
                    body = "\n".join(lines[start:start + CHUNK_LINES])
                    db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)",
                               (entry.path, start + 1, min(start + CHUNK_LINES, len(lines)), body))
                if entry.path.endswith(".py"):
                    self._index_python(db, entry.path, text)
            for reason, paths in quarantined.items():
                db.executemany("INSERT INTO quarantine VALUES (?, ?)", [(path, reason) for path in paths])
            db.commit()
        finally:
            db.close()
        os.replace(temp, final)  # atomic publication
        return quarantined

    @staticmethod
    def _index_python(db: sqlite3.Connection, path: str, text: str) -> None:
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return  # unparseable or hostile: still searchable as text
        is_test = Path(path).name.startswith("test_") or "/tests/" in f"/{path}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else ("test" if is_test and node.name.startswith("test") else "function")
                line = text.splitlines()[node.lineno - 1].strip() if node.lineno <= len(text.splitlines()) else ""
                db.execute("INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?)",
                           (node.name, kind, path, node.lineno, getattr(node, "end_lineno", node.lineno), line[:300]))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    db.execute("INSERT INTO imports VALUES (?, ?)", (path, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                db.execute("INSERT INTO imports VALUES (?, ?)", (path, node.module))

    def _connect(self, manifest_digest: str) -> sqlite3.Connection:
        path = self._path(manifest_digest)
        if not path.exists():
            raise FileNotFoundError(f"no index for {manifest_digest}; build it first")
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def search(self, manifest_digest: str, query: str, *, limit: int = 20) -> list[SourceRef]:
        """Exact symbol matches first, then path matches, then full-text matches."""
        db = self._connect(manifest_digest)
        try:
            words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)][:12]
            results: list[SourceRef] = []
            seen: set[tuple[str, int]] = set()

            def add(ref: SourceRef) -> None:
                key = (ref.path, ref.start_line)
                if key not in seen:
                    seen.add(key)
                    results.append(ref)

            for word in words:
                for row in db.execute("SELECT * FROM symbols WHERE name = ? ORDER BY path, start_line", (word,)):
                    add(SourceRef(row["path"], row["start_line"], row["end_line"], row["kind"], row["name"],
                                  100.0, f"exact {row['kind']} name match"))
            for word in words:
                for row in db.execute("SELECT path, lines FROM files WHERE path LIKE ? ESCAPE '\\' ORDER BY path",
                                      ("%" + word.replace("_", "\\_") + "%",)):
                    add(SourceRef(row["path"], 1, max(row["lines"], 1), "path", row["path"], 50.0, "path match"))
            if words:
                fts_query = " OR ".join(f'"{w}"' for w in words)
                for row in db.execute(
                    "SELECT path, start_line, end_line, bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ?"
                    " ORDER BY rank LIMIT ?", (fts_query, limit),
                ):
                    add(SourceRef(row["path"], int(row["start_line"]), int(row["end_line"]), "chunk", row["path"],
                                  float(-row["rank"]), "full-text match"))
            return results[:limit]
        finally:
            db.close()

    def defined_names(self, manifest_digest: str) -> set[str]:
        """Every function and class name defined in the snapshot (for documentation freshness)."""
        db = self._connect(manifest_digest)
        try:
            return {row[0] for row in db.execute("SELECT DISTINCT name FROM symbols")}
        finally:
            db.close()

    def tests_for(self, manifest_digest: str, module_path: str) -> list[SourceRef]:
        """Test functions in files that import the given module (by module name)."""
        module = module_path.removesuffix(".py").replace("/", ".").removeprefix("src.")
        short = module.rsplit(".", 1)[-1]
        db = self._connect(manifest_digest)
        try:
            rows = db.execute(
                "SELECT s.* FROM symbols s JOIN imports i ON s.path = i.path"
                " WHERE s.kind = 'test' AND (i.module = ? OR i.module = ? OR i.module LIKE ?)"
                " ORDER BY s.path, s.start_line", (module, short, f"%.{short}"),
            ).fetchall()
            return [SourceRef(r["path"], r["start_line"], r["end_line"], "test", r["name"], 40.0,
                              f"test in a file that imports {short}") for r in rows]
        finally:
            db.close()

    def manifest_digest_of(self, manifest_digest: str) -> str:
        db = self._connect(manifest_digest)
        try:
            return db.execute("SELECT value FROM meta WHERE key = 'manifest_digest'").fetchone()[0]
        finally:
            db.close()


def text_digest(text: str) -> str:
    return sha256_digest(text.encode("utf-8"))
