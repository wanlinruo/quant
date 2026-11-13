"""订单幂等键按交易模式隔离（#72）

paper 与 live 的确定性 client_order_id 可以相同；数据库内以
(mode, client_order_id) 唯一。降级回旧结构时，同一 client_order_id
只能保留最早写入的一行。

Revision ID: f4a2c8d19e73
Revises: d4e6a8c0b2f3
Create Date: 2026-11-13 03:10:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f4a2c8d19e73"
down_revision: str | None = "d4e6a8c0b2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("uq_orders_client_order_id"), "orders", type_="unique")
    op.create_unique_constraint(
        op.f("uq_orders_mode_client_order_id"), "orders", ["mode", "client_order_id"]
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM orders AS duplicate
        USING orders AS kept
        WHERE duplicate.client_order_id = kept.client_order_id
          AND duplicate.id > kept.id
        """
    )
    op.drop_constraint(op.f("uq_orders_mode_client_order_id"), "orders", type_="unique")
    op.create_unique_constraint(op.f("uq_orders_client_order_id"), "orders", ["client_order_id"])
