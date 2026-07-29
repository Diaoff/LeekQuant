"""Add kline sync guarantee infrastructure.

Introduces three changes that the K-line sync reconciler relies on:

1. A new ``kline_sync_failures`` table that tracks per-(parent task, ts_code)
   batch failures so the reconciler can promote repeated failures to
   permanent and stop re-dispatching them.
2. A UNIQUE constraint on ``task_runs.task_id``. The reconciler looks up
   batch tasks by ``task_id``; duplicate rows would cause ambiguous matches.
   Pre-existing duplicates are de-duplicated before the constraint is added
   (the row with the highest ``id`` wins, the rest are deleted).
3. A new ``dispatched`` value in the ``task_runs.status`` CHECK constraint,
   covering the window between dispatch and worker pickup.

The pre-existing ``status`` CHECK was unnamed in the M0 migration, so the
constraint name is resolved dynamically via ``pg_constraint`` before being
dropped; the replacement is explicitly named ``task_runs_status_check`` so
future migrations can address it deterministically.

Revision ID: 202607220001
Revises: 202607200001
Create Date: 2026-07-22 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202607220001"
down_revision: str | None = "202607200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE kline_sync_failures (
        id                     BIGSERIAL PRIMARY KEY,
        parent_task_id         BIGINT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
        batch_task_id          VARCHAR(128),
        ts_code                VARCHAR(16) NOT NULL,
        failure_count          INTEGER NOT NULL DEFAULT 1,
        last_error             TEXT,
        last_failed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        is_permanent_failure   BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (parent_task_id, ts_code)
    )
    """,
    "CREATE INDEX idx_kline_sync_failures_ts_code_permanent ON kline_sync_failures (ts_code, is_permanent_failure)",
    """
    DELETE FROM task_runs a USING task_runs b
    WHERE a.task_id = b.task_id
      AND a.id < b.id
      AND a.task_id IS NOT NULL
    """,
    "ALTER TABLE task_runs ADD CONSTRAINT uq_task_runs_task_id UNIQUE (task_id)",
    """
    DO $$
    DECLARE
        cname text;
    BEGIN
        SELECT conname INTO cname
        FROM pg_constraint
        WHERE conrelid = 'task_runs'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%status%';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE task_runs DROP CONSTRAINT %I', cname);
        END IF;
    END $$
    """,
    """
    ALTER TABLE task_runs ADD CONSTRAINT task_runs_status_check
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled', 'dispatched'))
    """,
]


DOWNGRADE_STATEMENTS = [
    """
    ALTER TABLE task_runs DROP CONSTRAINT IF EXISTS task_runs_status_check
    """,
    """
    DO $$
    DECLARE
        cname text;
    BEGIN
        SELECT conname INTO cname
        FROM pg_constraint
        WHERE conrelid = 'task_runs'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%status%';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE task_runs DROP CONSTRAINT %I', cname);
        END IF;
    END $$
    """,
    """
    ALTER TABLE task_runs ADD CONSTRAINT task_runs_status_check
        CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled'))
    """,
    "ALTER TABLE task_runs DROP CONSTRAINT IF EXISTS uq_task_runs_task_id",
    "DROP TABLE IF EXISTS kline_sync_failures",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
