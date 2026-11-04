-- FORGE user-level trust store, migration 0001.
--
-- This database lives in the user's application directory, NOT in a project
-- repository. It never travels with a cloned repository. Grants are created
-- only through a trusted channel by a human actor; repository files can only
-- produce proposals.

CREATE TABLE grants (
    grant_id         TEXT PRIMARY KEY CHECK (grant_id GLOB 'grant_*'),
    project_id       TEXT NOT NULL CHECK (project_id GLOB 'project_*'),
    actions_json     TEXT NOT NULL CHECK (json_valid(actions_json)),
    path_scope_json  TEXT NOT NULL CHECK (json_valid(path_scope_json)),
    justification    TEXT NOT NULL,
    proposal_origin  TEXT NOT NULL CHECK (proposal_origin IN ('user', 'repository', 'model')),
    proposal_digest  TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('approved', 'revoked')),
    approved_by      TEXT NOT NULL CHECK (approved_by GLOB 'actor_*'),
    -- The only channel that can approve. Enforced here as a second line of
    -- defense; the Python code checks it first.
    approved_channel TEXT NOT NULL CHECK (approved_channel = 'trusted_local'),
    approved_at      TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT,
    revoked_by       TEXT,
    record_version   INTEGER NOT NULL CHECK (record_version >= 1),
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
) STRICT;

CREATE INDEX grants_by_project ON grants (project_id, status);
