-- FORGE project database, migration 0003: evidence, assessments, findings (ticket F05).
--
-- Three different things, kept apart on purpose:
--   evidence    what was observed ("the program exited 0 and printed X")
--   assessment  what the evidence means for one criterion revision ("fail")
--   finding     a supported issue that stays visible until resolved
-- A decision (approval, waiver) is a fourth thing and never edits these.

CREATE TABLE evidence (
    evidence_id         TEXT PRIMARY KEY CHECK (evidence_id GLOB 'evidence_*'),
    project_id          TEXT NOT NULL REFERENCES projects (project_id),
    origin              TEXT NOT NULL CHECK (origin IN ('execution', 'review')),
    -- execution origin
    job_id              TEXT,
    attempt_number      INTEGER,
    snapshot_id         TEXT REFERENCES snapshots (snapshot_id),
    tool_ref            TEXT,
    environment_digest  TEXT,
    evaluator_digest    TEXT,
    -- review origin
    subject_ref_json    TEXT CHECK (subject_ref_json IS NULL OR json_valid(subject_ref_json)),
    reviewer_id         TEXT,
    observation_type    TEXT NOT NULL,
    observation_json    TEXT NOT NULL CHECK (json_valid(observation_json)),
    artifact_refs_json  TEXT NOT NULL CHECK (json_valid(artifact_refs_json)),
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    record_version      INTEGER NOT NULL CHECK (record_version >= 1),
    -- The two origins have mutually exclusive required fields. Review
    -- evidence never carries an invented job, attempt or snapshot.
    CHECK (
        (origin = 'execution' AND job_id IS NOT NULL AND attempt_number >= 1
            AND snapshot_id IS NOT NULL AND tool_ref IS NOT NULL
            AND environment_digest IS NOT NULL
            AND subject_ref_json IS NULL AND reviewer_id IS NULL)
        OR
        (origin = 'review' AND subject_ref_json IS NOT NULL AND reviewer_id IS NOT NULL
            AND job_id IS NULL AND attempt_number IS NULL AND snapshot_id IS NULL)
    )
) STRICT;

CREATE TABLE assessments (
    assessment_id           TEXT PRIMARY KEY CHECK (assessment_id GLOB 'assessment_*'),
    project_id              TEXT NOT NULL REFERENCES projects (project_id),
    criterion_id            TEXT NOT NULL,
    criterion_revision      INTEGER NOT NULL CHECK (criterion_revision >= 1),
    snapshot_id             TEXT REFERENCES snapshots (snapshot_id),
    evidence_refs_json      TEXT NOT NULL CHECK (json_valid(evidence_refs_json)),
    assessment_result       TEXT NOT NULL CHECK (assessment_result IN (
                                'pass', 'fail', 'inconclusive', 'not_run', 'error', 'not_applicable')),
    assessor_ref            TEXT NOT NULL,
    limitations_json        TEXT NOT NULL CHECK (json_valid(limitations_json)),
    summary_json            TEXT NOT NULL CHECK (json_valid(summary_json)),
    supersedes_assessment_id TEXT REFERENCES assessments (assessment_id),
    created_at              TEXT NOT NULL,
    created_by              TEXT NOT NULL,
    record_version          INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

CREATE INDEX assessments_by_criterion ON assessments (criterion_id, snapshot_id);

CREATE TABLE findings (
    finding_id          TEXT PRIMARY KEY CHECK (finding_id GLOB 'finding_*'),
    project_id          TEXT NOT NULL REFERENCES projects (project_id),
    rule_id             TEXT NOT NULL,
    claim               TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    status              TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'waived', 'false_positive')),
    confirmation        TEXT NOT NULL CHECK (confirmation IN ('suspected', 'confirmed')),
    path                TEXT,
    line                INTEGER,
    fingerprint         TEXT NOT NULL,
    snapshot_id         TEXT REFERENCES snapshots (snapshot_id),
    evidence_refs_json  TEXT NOT NULL CHECK (json_valid(evidence_refs_json)),
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    record_version      INTEGER NOT NULL CHECK (record_version >= 1)
) STRICT;

CREATE INDEX findings_by_fingerprint ON findings (fingerprint);
