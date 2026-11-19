"""Additive SQLite schema for AgenEditor 2.0 production data.

Project databases gain the tables below on open (``CREATE TABLE IF NOT EXISTS``); nothing existing is
altered, so single-agent projects stay readable and editable. Client and profile data that spans
projects lives in the workspace-level ``production.sqlite3``.
"""

from __future__ import annotations

PRODUCTION_SCHEMA_VERSION = 1

PROJECT_DDL = """
CREATE TABLE IF NOT EXISTS production_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS context_snapshots(
    hash TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches(
    id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, status TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL, deadline TEXT,
    data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clip_candidates(
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
    rank INTEGER NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clip_candidates_batch ON clip_candidates(batch_id, rank);
CREATE TABLE IF NOT EXISTS clips(
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
    candidate_id TEXT NOT NULL UNIQUE REFERENCES clip_candidates(id),
    status TEXT NOT NULL, current_revision INTEGER, writer_fence INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_batch ON clips(batch_id);
CREATE TABLE IF NOT EXISTS clip_plans(
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), version INTEGER NOT NULL,
    hash TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(clip_id, version)
);
CREATE TABLE IF NOT EXISTS clip_timelines(
    clip_id TEXT NOT NULL REFERENCES clips(id), revision INTEGER NOT NULL, data TEXT NOT NULL,
    attempt_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(clip_id, revision)
);
CREATE TABLE IF NOT EXISTS tasks(
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), clip_id TEXT REFERENCES clips(id),
    graph_key TEXT NOT NULL UNIQUE, type TEXT NOT NULL, cycle INTEGER NOT NULL, status TEXT NOT NULL,
    priority INTEGER NOT NULL, not_before TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, priority);
CREATE INDEX IF NOT EXISTS tasks_clip ON tasks(clip_id, cycle);
CREATE TABLE IF NOT EXISTS task_dependencies(
    task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY(task_id, depends_on), CHECK(task_id != depends_on)
);
CREATE TABLE IF NOT EXISTS task_attempts(
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), state TEXT NOT NULL,
    fencing_token INTEGER NOT NULL, lease_expires_at TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_attempts_task ON task_attempts(task_id, state);
CREATE TABLE IF NOT EXISTS capability_tokens(
    token_hash TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES task_attempts(id),
    scope TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints(
    id TEXT PRIMARY KEY, task_type TEXT NOT NULL, scope_key TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL, valid INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checkpoints_lookup ON checkpoints(task_type, scope_key, input_fingerprint, valid);
CREATE TABLE IF NOT EXISTS artifacts(
    id TEXT PRIMARY KEY, clip_id TEXT, kind TEXT NOT NULL, sha256 TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_reviews(
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_findings(
    id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES qa_reviews(id),
    clip_id TEXT NOT NULL REFERENCES clips(id), state TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals(
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), revoked INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS approvals_one_active ON approvals(clip_id) WHERE revoked = 0;
CREATE TABLE IF NOT EXISTS exports(
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id),
    approval_id TEXT NOT NULL REFERENCES approvals(id), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_manifests(
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), sha256 TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS production_events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_usage(
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, batch_id TEXT NOT NULL, task_type TEXT NOT NULL,
    data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_reservations(
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE, batch_id TEXT NOT NULL,
    amount_micros INTEGER NOT NULL, state TEXT NOT NULL
);
"""

WORKSPACE_DDL = """
CREATE TABLE IF NOT EXISTS clients(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_revisions(
    id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(scope_key, revision)
);
CREATE TABLE IF NOT EXISTS project_clients(
    project_id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id), bound_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resource_samples(measured_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS resource_samples_time ON resource_samples(measured_at);
"""
