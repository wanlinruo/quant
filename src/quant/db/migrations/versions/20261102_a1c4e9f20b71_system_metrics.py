"""资源采样表 system_metrics（#71 B 批，设计 5.5、7.2）

Revision ID: a1c4e9f20b71
Revises: 752bb5d32564
Create Date: 2026-11-02 02:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c4e9f20b71"
down_revision: str | None = "752bb5d32564"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("cpu_pct", sa.Float(), nullable=True),
        sa.Column("mem_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mem_limit", sa.BigInteger(), nullable=True),
        sa.Column("disk_used", sa.BigInteger(), nullable=True),
        sa.Column("disk_total", sa.BigInteger(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_metrics")),
    )
    op.create_index(op.f("ix_system_metrics_ts"), "system_metrics", ["ts"])
    op.create_index("ix_system_metrics_kind_name_ts", "system_metrics", ["kind", "name", "ts"])


def downgrade() -> None:
    op.drop_index("ix_system_metrics_kind_name_ts", table_name="system_metrics")
    op.drop_index(op.f("ix_system_metrics_ts"), table_name="system_metrics")
    op.drop_table("system_metrics")
