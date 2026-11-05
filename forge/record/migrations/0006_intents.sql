-- FORGE project database, migration 0006: recorded intents (ticket F08).
--
-- An intent is the user's request in their own words ("Build a utility that
-- reads packets..."). It is kept verbatim and linked to the planning task it
-- created. Conversation is an input and a view, not the authoritative record.

CREATE TABLE intents (
    intent_id          TEXT PRIMARY KEY CHECK (intent_id GLOB 'intent_*'),
    project_id         TEXT NOT NULL REFERENCES projects (project_id),
    text               TEXT NOT NULL,
    mode               TEXT NOT NULL CHECK (mode IN ('build', 'pair', 'learn', 'review')),
    artifact_refs_json TEXT NOT NULL CHECK (json_valid(artifact_refs_json)),
    planning_task_id   TEXT NOT NULL REFERENCES tasks (task_id),
    created_at         TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    record_version     INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;
