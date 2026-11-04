-- FORGE project database, migration 0007: recorded model calls (ticket F09).
--
-- Every model call is recorded: which exact configuration, which slot, the
-- digests of what was sent and received, usage, timing and outcome status.
-- Prompts are stored only as digests by default; secrets were redacted
-- before sending. Unknown token counts stay NULL, never estimated.

CREATE TABLE model_calls (
    call_id            TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects (project_id),
    task_id            TEXT,
    model_config_id    TEXT NOT NULL,
    config_digest      TEXT NOT NULL,
    slot_id            TEXT NOT NULL,
    status             TEXT NOT NULL,
    prompt_digest      TEXT NOT NULL,
    output_digest      TEXT NOT NULL,
    output_json        TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    errors_json        TEXT NOT NULL CHECK (json_valid(errors_json)),
    redactions_json    TEXT NOT NULL CHECK (json_valid(redactions_json)),
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    wall_ms            INTEGER NOT NULL,
    created_at         TEXT NOT NULL,
    created_by         TEXT NOT NULL
) STRICT;

CREATE INDEX model_calls_by_task ON model_calls (task_id);
