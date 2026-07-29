"""Rebuild K-line sync queue with jobs and items tables.

Introduces the two tables that back the rebuilt K-line sync pipeline:

1. ``kline_sync_jobs`` — a single row per sync run (incremental or full),
   tracking overall progress (scope_total / scope_done / scope_failed),
   the set of permanently-failed ts_codes, and a JSONB ``config`` blob.
2. ``kline_sync_items`` — one row per (job, ts_code, start_date, end_date)
   work unit, with retry counters, worker assignment, and a partial index
   on ``last_attempt_at`` for stuck-item detection. Items cascade-delete
   with their parent job.

Revision ID: rebuild_kline_sync_queue
Revises: 202607220001
Create Date: 2026-07-23 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "rebuild_kline_sync_queue"
down_revision: str | None = "202607220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE kline_sync_jobs (
        id                      BIGSERIAL PRIMARY KEY,
        job_type                VARCHAR(32) NOT NULL,
        status                  VARCHAR(20) NOT NULL DEFAULT 'running',
        scope_total             INTEGER NOT NULL DEFAULT 0,
        scope_done              INTEGER NOT NULL DEFAULT 0,
        scope_failed            INTEGER NOT NULL DEFAULT 0,
        permanent_failure_codes TEXT[] NOT NULL DEFAULT '{}',
        config                  JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at              TIMESTAMPTZ,
        completed_at            TIMESTAMPTZ,
        error                   TEXT,
        CONSTRAINT kline_sync_jobs_status_check CHECK (status IN ('running', 'completed', 'failed')),
        CONSTRAINT kline_sync_jobs_type_check CHECK (job_type IN ('incremental', 'full'))
    )
    """,
    "CREATE INDEX idx_kline_sync_jobs_created_at ON kline_sync_jobs (created_at DESC)",
    "CREATE INDEX idx_kline_sync_jobs_status ON kline_sync_jobs (status)",
    """
    CREATE TABLE kline_sync_items (
        id              BIGSERIAL PRIMARY KEY,
        job_id          BIGINT NOT NULL REFERENCES kline_sync_jobs(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16) NOT NULL,
        start_date      DATE NOT NULL,
        end_date        DATE NOT NULL,
        status          VARCHAR(20) NOT NULL DEFAULT 'pending',
        attempts        INTEGER NOT NULL DEFAULT 0,
        last_error      TEXT,
        last_attempt_at TIMESTAMPTZ,
        worker_id       VARCHAR(64),
        CONSTRAINT kline_sync_items_status_check CHECK (status IN ('pending', 'running', 'done', 'permanently_failed')),
        CONSTRAINT kline_sync_items_unique UNIQUE (job_id, ts_code, start_date, end_date)
    )
    """,
    "CREATE INDEX idx_kline_sync_items_job_status ON kline_sync_items (job_id, status)",
    "CREATE INDEX idx_kline_sync_items_stuck ON kline_sync_items (last_attempt_at) WHERE status = 'running'",
]


DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS kline_sync_items",
    "DROP TABLE IF EXISTS kline_sync_jobs",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
