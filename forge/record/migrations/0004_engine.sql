-- FORGE project database, migration 0004: plan revisions and completion (ticket F06).

ALTER TABLE projects ADD COLUMN plan_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE tasks ADD COLUMN supersedes_task_id TEXT;
ALTER TABLE tasks ADD COLUMN superseded_by_task_id TEXT;
-- How the task was judged complete: which snapshot, which assessments,
-- which waivers. NULL until the task completes.
ALTER TABLE tasks ADD COLUMN completion_json TEXT CHECK (completion_json IS NULL OR json_valid(completion_json));

-- Every accepted plan change is kept; replanning never erases a revision.
CREATE TABLE plan_revisions (
    plan_revision  INTEGER PRIMARY KEY CHECK (plan_revision >= 1),
    project_id     TEXT NOT NULL REFERENCES projects (project_id),
    summary_json   TEXT NOT NULL CHECK (json_valid(summary_json)),
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL
) STRICT;

-- "Latest assessment" must follow the event log, not wall-clock time: two
-- assessments recorded in the same millisecond would otherwise be ordered
-- by chance.
ALTER TABLE assessments ADD COLUMN recorded_seq INTEGER;
CREATE INDEX assessments_by_sequence ON assessments (criterion_id, recorded_seq);
