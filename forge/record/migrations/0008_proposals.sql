-- FORGE project database, migration 0008: change proposals and applications (ticket F12).
--
-- A proposal is the exact difference between a base snapshot and a candidate
-- snapshot. Its digest covers every changed path with its before and after
-- content digests; approval binds to that digest. An application records
-- what was actually written to the user's files, after the fact.

CREATE TABLE proposals (
    proposal_id           TEXT PRIMARY KEY CHECK (proposal_id GLOB 'proposal_*'),
    project_id            TEXT NOT NULL REFERENCES projects (project_id),
    task_id               TEXT REFERENCES tasks (task_id),
    kind                  TEXT NOT NULL CHECK (kind IN ('implementation', 'test_change')),
    base_snapshot_id      TEXT NOT NULL REFERENCES snapshots (snapshot_id),
    candidate_snapshot_id TEXT NOT NULL REFERENCES snapshots (snapshot_id),
    proposal_digest       TEXT NOT NULL,
    changes_json          TEXT NOT NULL CHECK (json_valid(changes_json)),
    status                TEXT NOT NULL CHECK (status IN ('open', 'applied', 'rejected')),
    created_at            TEXT NOT NULL,
    created_by            TEXT NOT NULL,
    record_version        INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

CREATE TABLE applications (
    apply_id      TEXT PRIMARY KEY,
    proposal_id   TEXT NOT NULL REFERENCES proposals (proposal_id),
    outcome       TEXT NOT NULL CHECK (outcome IN ('applied', 'rolled_back', 'conflict')),
    detail_json   TEXT NOT NULL CHECK (json_valid(detail_json)),
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL
) STRICT;
