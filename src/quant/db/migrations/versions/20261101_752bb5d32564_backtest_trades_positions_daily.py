"""回测逐笔交易、每日持仓与每日现金 / 择时系数 / 子仓盈亏（#67 B 批，设计 7.1）

三张新表都随 backtest_runs 级联删除；降级只删除这三张表，不影响已有数据。

Revision ID: 752bb5d32564
Revises: 7723dfb3f2e6
Create Date: 2026-11-01 18:04:39.790907+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "752bb5d32564"
down_revision: str | None = "7723dfb3f2e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_daily",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("cash_weight", sa.Float(), nullable=False),
        sa.Column("timing_factor", sa.Float(), nullable=True),
        sa.Column("sleeve_pnl", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name=op.f("fk_backtest_daily_run_id_backtest_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "date", name=op.f("pk_backtest_daily")),
    )
    op.create_table(
        "backtest_positions",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("sleeve", sa.String(length=16), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name=op.f("fk_backtest_positions_run_id_backtest_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "date", "symbol", "sleeve", name=op.f("pk_backtest_positions")
        ),
    )
    op.create_table(
        "backtest_trades",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("sleeve", sa.String(length=16), nullable=False),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("open_price", sa.Float(), nullable=False),
        sa.Column("close_date", sa.Date(), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("ret", sa.Float(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(length=64), nullable=True),
        sa.Column("adjustments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name=op.f("fk_backtest_trades_run_id_backtest_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "seq", name=op.f("pk_backtest_trades")),
    )


def downgrade() -> None:
    op.drop_table("backtest_trades")
    op.drop_table("backtest_positions")
    op.drop_table("backtest_daily")
