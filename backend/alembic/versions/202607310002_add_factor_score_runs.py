"""Add factor_score_runs table and score_run_id to scoring_rank/factor_values.

Revision ID: 202607310002
Revises: 202607310001
Create Date: 2026-07-31 00:02:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202607310002"
down_revision: str | None = "202607310001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE factor_score_runs (
        id                BIGSERIAL PRIMARY KEY,
        score_date        DATE NOT NULL,
        scope_type        VARCHAR(32) NOT NULL DEFAULT 'all',
        scope_value       VARCHAR(128),
        definition_snapshot JSONB NOT NULL DEFAULT '{}'::JSONB,
        definition_hash   VARCHAR(64),
        universe_hash     VARCHAR(64),
        data_cutoff       DATE,
        engine_version    VARCHAR(32) NOT NULL DEFAULT '1',
        status            VARCHAR(20) NOT NULL DEFAULT 'pending',
        coverage          JSONB,
        factor_counts     JSONB,
        error_summary     JSONB,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at       TIMESTAMPTZ
    )
    """,
    "ALTER TABLE scoring_rank ADD COLUMN IF NOT EXISTS score_run_id BIGINT REFERENCES factor_score_runs(id)",
    "ALTER TABLE factor_values ADD COLUMN IF NOT EXISTS score_run_id BIGINT REFERENCES factor_score_runs(id)",
    "CREATE INDEX IF NOT EXISTS idx_scoring_rank_score_run_id ON scoring_rank(score_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_factor_values_score_run_id ON factor_values(score_run_id)",
]


DOWNGRADE_STATEMENTS = [
    "DROP INDEX IF EXISTS idx_factor_values_score_run_id",
    "DROP INDEX IF EXISTS idx_scoring_rank_score_run_id",
    "ALTER TABLE factor_values DROP COLUMN IF EXISTS score_run_id",
    "ALTER TABLE scoring_rank DROP COLUMN IF EXISTS score_run_id",
    "DROP TABLE IF EXISTS factor_score_runs",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)