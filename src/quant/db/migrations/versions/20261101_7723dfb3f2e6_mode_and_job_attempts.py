"""信号与任务记录按交易模式区分；信号明细；任务尝试记录（#65，设计 7.4）

已有的 signals、job_runs 行回填 mode = 'paper'（服务器上唯一的真实数据是模拟盘）。
降级时同一 (strategy, signal_date) / (job_name, trade_date) 只保留 paper 行，其余删除。

Revision ID: 7723dfb3f2e6
Revises: dc3b19a15e9b
Create Date: 2026-11-01 03:35:45.793134+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7723dfb3f2e6"
down_revision: str | None = "dc3b19a15e9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_MODE = "paper"


def _add_mode(table: str) -> None:
    op.add_column(table, sa.Column("mode", sa.String(length=8), nullable=True))
    op.execute(sa.text(f"UPDATE {table} SET mode = :m").bindparams(m=BACKFILL_MODE))
    op.alter_column(table, "mode", nullable=False)


def upgrade() -> None:
    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "result IN ('running', 'success', 'failed')", name=op.f("ck_job_attempts_result")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_attempts")),
        sa.UniqueConstraint(
            "mode", "job_name", "trade_date", "attempt",
            name=op.f("uq_job_attempts_mode_job_name_trade_date_attempt"),
        ),
    )  # fmt: skip
    op.create_index(
        "ix_job_attempts_mode_trade_date", "job_attempts", ["mode", "trade_date"], unique=False
    )
    _add_mode("job_runs")
    op.drop_constraint(op.f("uq_job_runs_job_name_trade_date"), "job_runs", type_="unique")
    op.create_unique_constraint(
        op.f("uq_job_runs_mode_job_name_trade_date"), "job_runs", ["mode", "job_name", "trade_date"]
    )
    _add_mode("signals")
    op.add_column("signals", sa.Column("detail", postgresql.JSONB(), nullable=True))
    op.drop_constraint(op.f("uq_signals_strategy_signal_date"), "signals", type_="unique")
    op.create_unique_constraint(
        op.f("uq_signals_mode_strategy_signal_date"), "signals", ["mode", "strategy", "signal_date"]
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM signals WHERE mode <> :m").bindparams(m=BACKFILL_MODE))
    op.execute(sa.text("DELETE FROM job_runs WHERE mode <> :m").bindparams(m=BACKFILL_MODE))
    op.drop_constraint(op.f("uq_signals_mode_strategy_signal_date"), "signals", type_="unique")
    op.create_unique_constraint(
        op.f("uq_signals_strategy_signal_date"), "signals", ["strategy", "signal_date"]
    )
    op.drop_column("signals", "detail")
    op.drop_column("signals", "mode")
    op.drop_constraint(op.f("uq_job_runs_mode_job_name_trade_date"), "job_runs", type_="unique")
    op.create_unique_constraint(
        op.f("uq_job_runs_job_name_trade_date"), "job_runs", ["job_name", "trade_date"]
    )
    op.drop_column("job_runs", "mode")
    op.drop_index("ix_job_attempts_mode_trade_date", table_name="job_attempts")
    op.drop_table("job_attempts")
