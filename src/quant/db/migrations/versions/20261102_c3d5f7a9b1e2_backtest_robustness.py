"""回测稳健性检验的子任务：backtest_runs.parent_id 与 variant（#68）

Revision ID: c3d5f7a9b1e2
Revises: a1c4e9f20b71
Create Date: 2026-11-02 04:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d5f7a9b1e2"
down_revision: str | None = "a1c4e9f20b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.add_column(
        "backtest_runs",
        sa.Column("variant", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_backtest_runs_parent_id_backtest_runs"),
        "backtest_runs",
        "backtest_runs",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_backtest_runs_parent_id"), "backtest_runs", ["parent_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_backtest_runs_parent_id"), table_name="backtest_runs")
    op.drop_constraint(
        op.f("fk_backtest_runs_parent_id_backtest_runs"), "backtest_runs", type_="foreignkey"
    )
    op.drop_column("backtest_runs", "variant")
    op.drop_column("backtest_runs", "parent_id")
