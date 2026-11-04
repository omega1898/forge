-- FORGE project database, migration 0005: jobs and the dispatch outbox (ticket F07).

CREATE TABLE jobs (
    job_id              TEXT NOT NULL CHECK (job_id GLOB 'job_*'),
    attempt_number      INTEGER NOT NULL CHECK (attempt_number >= 1),
    project_id          TEXT NOT NULL REFERENCES projects (project_id),
    task_id             TEXT NOT NULL REFERENCES tasks (task_id),
    host_id             TEXT NOT NULL,
    lease_epoch         INTEGER NOT NULL CHECK (lease_epoch >= 1),
    spec_json           TEXT NOT NULL CHECK (json_valid(spec_json)),
    job_digest          TEXT NOT NULL,
    effect_class        TEXT NOT NULL,
    retry_class         TEXT NOT NULL,
    execution_status    TEXT NOT NULL CHECK (execution_status IN (
                            'queued', 'leased', 'running', 'succeeded', 'failed', 'cancelled', 'lost')),
    host_event_seq      INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    reconciliation      TEXT CHECK (reconciliation IS NULL OR reconciliation IN ('required', 'resolved')),
    outcome_json        TEXT CHECK (outcome_json IS NULL OR json_valid(outcome_json)),
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    record_version      INTEGER NOT NULL CHECK (record_version >= 1),
    PRIMARY KEY (job_id, attempt_number)
) STRICT;

CREATE INDEX jobs_by_status ON jobs (execution_status);
CREATE INDEX jobs_by_task ON jobs (task_id);

-- Work to perform after a commit (sending a job to a host). Written in the
-- same transaction as the job, so a crash can never lose or invent a send.
CREATE TABLE outbox (
    outbox_id       TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    lease_epoch     INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('submit', 'cancel')),
    state           TEXT NOT NULL CHECK (state IN ('pending', 'acknowledged')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
) STRICT;

CREATE INDEX outbox_pending ON outbox (state);
