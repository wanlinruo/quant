"""盘中最新报价 intraday_quotes（#93）

Revision ID: d4e6a8c0b2f3
Revises: c3d5f7a9b1e2
Create Date: 2026-11-02 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6a8c0b2f3"
down_revision: str | None = "c3d5f7a9b1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intraday_quotes",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("prev_close", sa.Float(), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("quote_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("symbol", name=op.f("pk_intraday_quotes")),
    )


def downgrade() -> None:
    op.drop_table("intraday_quotes")
