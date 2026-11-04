-- FORGE user-level trust store, migration 0002: approved source roots.
--
-- A project names its source roots by label ("main"). Which host directory a
-- label means is user-level trust, approved by a human, never read from a
-- repository. A cloned repository therefore cannot point FORGE at ~/.ssh.

CREATE TABLE source_roots (
    project_id   TEXT NOT NULL CHECK (project_id GLOB 'project_*'),
    label        TEXT NOT NULL,
    host_path    TEXT NOT NULL,
    approved_by  TEXT NOT NULL CHECK (approved_by GLOB 'actor_*'),
    approved_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, label)
) STRICT;
