-- FORGE project database, migration 0002: artifacts and snapshots (ticket F03).
--
-- Both tables are projections rebuilt from events. Blob bytes live in the
-- content-addressed store on disk; these rows only name them.

CREATE TABLE artifacts (
    artifact_id    TEXT PRIMARY KEY CHECK (artifact_id GLOB 'artifact_*'),
    project_id     TEXT NOT NULL REFERENCES projects (project_id),
    digest         TEXT NOT NULL CHECK (length(digest) = 71 AND digest GLOB 'sha256:*'),
    size           INTEGER NOT NULL CHECK (size >= 0),
    content_type   TEXT NOT NULL,
    artifact_kind  TEXT NOT NULL,
    sensitivity    TEXT NOT NULL CHECK (sensitivity IN ('public', 'project', 'sensitive')),
    retention      TEXT NOT NULL CHECK (retention IN ('keep', 'ephemeral')),
    producer_ref   TEXT,
    tombstoned_at  TEXT,
    tombstone_reason TEXT,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    record_version INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

CREATE INDEX artifacts_by_digest ON artifacts (digest);

CREATE TABLE snapshots (
    snapshot_id             TEXT PRIMARY KEY CHECK (snapshot_id GLOB 'snapshot_*'),
    project_id              TEXT NOT NULL REFERENCES projects (project_id),
    manifest_digest         TEXT NOT NULL CHECK (length(manifest_digest) = 71),
    manifest_artifact_id    TEXT NOT NULL REFERENCES artifacts (artifact_id),
    source_root             TEXT NOT NULL,
    base_commit             TEXT,
    capture_policy_revision INTEGER NOT NULL,
    file_count              INTEGER NOT NULL CHECK (file_count >= 0),
    total_bytes             INTEGER NOT NULL CHECK (total_bytes >= 0),
    excluded_count          INTEGER NOT NULL CHECK (excluded_count >= 0),
    dirty_paths_json        TEXT NOT NULL CHECK (json_valid(dirty_paths_json)),
    parent_snapshot_id      TEXT REFERENCES snapshots (snapshot_id),
    origin                  TEXT NOT NULL CHECK (origin IN ('capture', 'candidate')),
    created_at              TEXT NOT NULL,
    created_by              TEXT NOT NULL,
    record_version          INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

CREATE INDEX snapshots_by_manifest ON snapshots (manifest_digest);
