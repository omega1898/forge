-- FORGE project database, migration 0001: core record.
--
-- Two kinds of table live here:
--
--   AUTHORITATIVE (append-only history; never rebuilt):
--     events            every accepted state change, in order
--     command_receipts  the stored answer to every accepted command
--
--   PROJECTIONS (current-state views; can be deleted and rebuilt by
--   replaying events):
--     projects, records, tasks, task_dependencies, decisions
--
-- Authoritative tables must never have foreign keys into projections,
-- because projections are wiped during a rebuild.
--
-- Snapshots, artifacts, jobs, evidence, assessments and the outbox arrive in
-- later migrations together with the code that uses them (tickets F03-F07).
--
-- STRICT tables make SQLite enforce declared column types.
-- This file is immutable once released: its checksum is recorded in
-- schema_migrations and verified every time a database is opened.

CREATE TABLE events (
    event_seq            INTEGER PRIMARY KEY CHECK (event_seq >= 1),
    event_id             TEXT NOT NULL UNIQUE CHECK (event_id GLOB 'event_*'),
    project_id           TEXT NOT NULL CHECK (project_id GLOB 'project_*'),
    command_id           TEXT NOT NULL CHECK (command_id GLOB 'command_*'),
    event_type           TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL CHECK (event_schema_version >= 1),
    actor_id             TEXT NOT NULL CHECK (actor_id GLOB 'actor_*'),
    occurred_at          TEXT NOT NULL,
    causation_id         TEXT,
    payload_json         TEXT NOT NULL CHECK (json_valid(payload_json))
) STRICT;

CREATE INDEX events_by_command ON events (command_id);

CREATE TRIGGER events_are_append_only_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER events_are_append_only_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE command_receipts (
    command_id      TEXT PRIMARY KEY CHECK (command_id GLOB 'command_*'),
    actor_id        TEXT NOT NULL CHECK (actor_id GLOB 'actor_*'),
    idempotency_key TEXT NOT NULL,
    method          TEXT NOT NULL,
    request_digest  TEXT NOT NULL CHECK (length(request_digest) = 71 AND request_digest GLOB 'sha256:*'),
    accepted_at     TEXT NOT NULL,
    response_json   TEXT NOT NULL CHECK (json_valid(response_json)),
    -- A key is scoped to the actor that chose it, so one client's key can
    -- never return another client's stored response.
    UNIQUE (actor_id, idempotency_key)
) STRICT;

CREATE TRIGGER command_receipts_are_append_only_no_update
BEFORE UPDATE ON command_receipts
BEGIN
    SELECT RAISE(ABORT, 'command receipts are append-only');
END;

CREATE TRIGGER command_receipts_are_append_only_no_delete
BEFORE DELETE ON command_receipts
BEGIN
    SELECT RAISE(ABORT, 'command receipts are append-only');
END;

-- ------------------------------------------------------------ projections --

CREATE TABLE projects (
    project_id        TEXT PRIMARY KEY CHECK (project_id GLOB 'project_*'),
    -- One project per database file: each project has its own write queue,
    -- database, grants and artifact namespace.
    singleton         INTEGER NOT NULL DEFAULT 1 UNIQUE CHECK (singleton = 1),
    name              TEXT NOT NULL,
    objective         TEXT NOT NULL,
    controller_id     TEXT NOT NULL CHECK (controller_id GLOB 'controller_*'),
    source_roots_json TEXT NOT NULL CHECK (json_valid(source_roots_json)),
    profile_id        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    record_version    INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

-- Less frequently queried records (requirements, criteria) share one table.
-- Their bodies are JSON, but identity, kind and revision are real columns.
CREATE TABLE records (
    record_id      TEXT PRIMARY KEY,
    record_kind    TEXT NOT NULL CHECK (record_kind IN ('requirement', 'criterion')),
    project_id     TEXT NOT NULL REFERENCES projects (project_id),
    revision       INTEGER NOT NULL CHECK (revision >= 1),
    body_json      TEXT NOT NULL CHECK (json_valid(body_json)),
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    record_version INTEGER NOT NULL CHECK (record_version >= 1),
    CHECK (record_id GLOB (record_kind || '_*'))
) STRICT;

-- Tasks get real columns because the scheduler reads them constantly.
CREATE TABLE tasks (
    task_id               TEXT PRIMARY KEY CHECK (task_id GLOB 'task_*'),
    project_id            TEXT NOT NULL REFERENCES projects (project_id),
    task_type             TEXT NOT NULL CHECK (task_type IN ('prepare', 'execute', 'assess', 'decide')),
    title                 TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN (
                              'queued', 'running', 'blocked', 'waiting_for_user',
                              'completed', 'failed', 'cancelled')),
    requirement_refs_json TEXT NOT NULL CHECK (json_valid(requirement_refs_json)),
    criterion_refs_json   TEXT NOT NULL CHECK (json_valid(criterion_refs_json)),
    input_refs_json       TEXT NOT NULL CHECK (json_valid(input_refs_json)),
    authority_ref         TEXT CHECK (authority_ref IS NULL OR authority_ref GLOB 'grant_*'),
    budget_json           TEXT NOT NULL CHECK (json_valid(budget_json)),
    workflow_revision     INTEGER NOT NULL CHECK (workflow_revision >= 1),
    created_at            TEXT NOT NULL,
    created_by            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    record_version        INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

CREATE INDEX tasks_by_status ON tasks (project_id, status);

CREATE TABLE task_dependencies (
    task_id            TEXT NOT NULL REFERENCES tasks (task_id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks (task_id),
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
) STRICT;

CREATE TABLE decisions (
    decision_id     TEXT PRIMARY KEY CHECK (decision_id GLOB 'decision_*'),
    project_id      TEXT NOT NULL REFERENCES projects (project_id),
    decision_kind   TEXT NOT NULL CHECK (decision_kind IN (
                        'apply_change', 'waiver', 'architecture', 'grant', 'release')),
    subject_ref_json TEXT NOT NULL CHECK (json_valid(subject_ref_json)),
    question        TEXT NOT NULL,
    proposal_digest TEXT,
    disposition     TEXT NOT NULL CHECK (disposition IN (
                        'pending', 'approved', 'rejected', 'withdrawn', 'expired')),
    reason          TEXT,
    actor_id        TEXT,
    authority_ref   TEXT,
    expires_at      TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    record_version  INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;
