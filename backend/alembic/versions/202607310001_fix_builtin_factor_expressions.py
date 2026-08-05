"""Fix builtin factor expressions and add builtin column.

Updates the 8 built-in factor expressions to use parser-compatible syntax
and adds a ``builtin`` boolean column to ``factor_definitions``.

Only rows whose expression still matches the original broken pattern are
updated, so user-modified expressions are preserved.

Revision ID: 202607310001
Revises: 202607290001
Create Date: 2026-07-31 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202607310001"
down_revision: str | None = "202607290001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# old → new expression mappings
EXPRESSION_FIXES: dict[str, tuple[str, str]] = {
    "pe_ttm": ("stock_fundamentals.pe_ttm", "pe_ttm"),
    "pb": ("stock_fundamentals.pb", "pb"),
    "roe": ("stock_fundamentals.roe", "roe"),
    "revenue_growth": ("stock_fundamentals.revenue_growth", "revenue_growth"),
    "mom_20d": ("close / close_20d - 1", "$close / REF($close, 20) - 1"),
    "mom_60d": ("close / close_60d - 1", "$close / REF($close, 60) - 1"),
    "rsi6": ("MyTT.RSI(close, 6)", "RSI($close, 6)"),
    "vol_20d": ("STD(returns, 20)", "STD($close / REF($close, 1) - 1, 20)"),
}


def upgrade() -> None:
    op.execute(
        "ALTER TABLE factor_definitions ADD COLUMN IF NOT EXISTS builtin BOOLEAN NOT NULL DEFAULT TRUE"
    )

    for name, (old_expr, new_expr) in EXPRESSION_FIXES.items():
        op.execute(
            f"""UPDATE factor_definitions
                SET expression = '{new_expr}',
                    updated_at = NOW()
                WHERE name = '{name}'
                  AND expression = '{old_expr}'"""
        )


def downgrade() -> None:
    for name, (old_expr, new_expr) in EXPRESSION_FIXES.items():
        op.execute(
            f"""UPDATE factor_definitions
                SET expression = '{old_expr}',
                    updated_at = NOW()
                WHERE name = '{name}'
                  AND expression = '{new_expr}'"""
        )

    op.execute("ALTER TABLE factor_definitions DROP COLUMN IF EXISTS builtin")