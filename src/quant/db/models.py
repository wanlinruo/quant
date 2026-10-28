"""PostgreSQL 表定义（PROJECT_PLAN 7.4）。表结构变更一律通过 Alembic 迁移。

约定：
- 金额、价格用 Numeric（对应领域模型的 Decimal）；权重、回撤用 float。
- 时间戳一律 timestamptz；交易日用 date（美东）。
- 状态类字段用字符串 + CHECK 约束，便于迁移。
- orders / fills / 快照带 mode 列（sim / paper / live），同一个库中模拟盘与实盘数据不混淆。
- 不存储任何密钥；账户号也不入库。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 约束命名规则，保证 Alembic 生成的迁移名称稳定
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MODES = ("sim", "paper", "live")
Money = Numeric(20, 6)
Price = Numeric(18, 6)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRun(Base):
    """定时任务运行记录；(job_name, trade_date) 唯一，保证幂等（PROJECT_PLAN 第 8 章）。"""

    __tablename__ = "job_runs"
    STATUSES = ("running", "success", "failed")

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64))
    trade_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("job_name", "trade_date"),
        CheckConstraint(_in("status", STATUSES), name="status"),
    )


class Signal(Base):
    """每日目标仓位。"""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64))
    signal_date: Mapped[date] = mapped_column(Date)
    target_weights: Mapped[dict[str, float]] = mapped_column(JSONB)
    data_as_of: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("strategy", "signal_date"),)


class Order(Base):
    __tablename__ = "orders"
    STATUSES = ("SUBMITTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "EXPIRED")

    id: Mapped[int] = mapped_column(primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(96), unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[Decimal | None] = mapped_column(Price)
    # 下单时的参考价（模拟盘阶段为延迟行情），用于评估执行偏差（issue #3 方案 3）
    reference_price: Mapped[Decimal | None] = mapped_column(Price)
    status: Mapped[str] = mapped_column(String(20))
    filled_qty: Mapped[int] = mapped_column(Integer, server_default="0")
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Price)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("mode", MODES), name="mode"),
        CheckConstraint(_in("side", ("BUY", "SELL")), name="side"),
        CheckConstraint(_in("order_type", ("LMT", "MKT")), name="order_type"),
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("filled_qty >= 0 AND filled_qty <= qty", name="filled_qty"),
    )


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Price)
    commission: Mapped[Decimal] = mapped_column(Money)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_in("mode", MODES), name="mode"),
        CheckConstraint(_in("side", ("BUY", "SELL")), name="side"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("commission >= 0", name="commission"),
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16))
    qty: Mapped[int] = mapped_column(Integer)
    avg_cost: Mapped[Decimal] = mapped_column(Price)
    market_value: Mapped[Decimal] = mapped_column(Money)

    __table_args__ = (
        UniqueConstraint("snapshot_date", "mode", "symbol"),
        CheckConstraint(_in("mode", MODES), name="mode"),
    )


class AccountSnapshotRow(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(8))
    cash: Mapped[Decimal] = mapped_column(Money)
    net_liquidation: Mapped[Decimal] = mapped_column(Money)

    __table_args__ = (
        UniqueConstraint("snapshot_date", "mode"),
        CheckConstraint(_in("mode", MODES), name="mode"),
    )


class BacktestRun(Base):
    """回测结果，兼作任务队列：worker 用 FOR UPDATE SKIP LOCKED 领取 queued 任务。"""

    __tablename__ = "backtest_runs"
    STATUSES = ("queued", "running", "done", "failed")

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint('start <= "end"', name="date_range"),
        Index("ix_backtest_runs_status_created_at", "status", "created_at"),
    )


class BacktestEquity(Base):
    __tablename__ = "backtest_equity"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True
    )
    equity_date: Mapped[date] = mapped_column("date", Date, primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Money)
    drawdown: Mapped[float] = mapped_column(Float)


class SystemFlag(Base):
    """系统开关，如 trading_enabled（熔断开关，PROJECT_PLAN 6.4）。面板只读。"""

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
