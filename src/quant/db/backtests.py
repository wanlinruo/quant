"""回测记录的读写：backtest_runs（兼作任务队列）与 backtest_equity。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import Engine, create_engine, delete, select, update
from sqlalchemy.orm import Session

from quant.db.models import (
    BacktestDaily,
    BacktestEquity,
    BacktestPosition,
    BacktestRun,
    BacktestTrade,
)

if TYPE_CHECKING:
    from quant.engine.ledger import Ledger


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def create_run(
    session: Session,
    strategy: str,
    params: Mapping[str, Any],
    start: date,
    end: date,
    status: str = "queued",
) -> int:
    run = BacktestRun(strategy=strategy, params=dict(params), start=start, end=end, status=status)
    if status == "running":
        run.started_at = datetime.now(UTC)
    session.add(run)
    session.flush()
    return run.id


def complete_run(
    session: Session,
    run_id: int,
    metrics: Mapping[str, Any],
    equity: pd.Series,
    drawdown: pd.Series,
    ledger: Ledger | None = None,
) -> None:
    """写入指标、净值曲线与（可选）逐笔交易 / 每日持仓，状态改为 done。重复调用会覆盖。"""
    for model in (BacktestEquity, BacktestTrade, BacktestPosition, BacktestDaily):
        session.execute(delete(model).where(model.run_id == run_id))
    if ledger is not None:
        session.add_all(
            BacktestTrade(run_id=run_id, seq=t.seq, symbol=t.symbol, sleeve=t.sleeve,
                          open_date=t.open_date, open_price=t.open_price, close_date=t.close_date,
                          close_price=t.close_price, qty=t.qty, ret=t.ret, pnl=t.pnl,
                          exit_reason=t.exit_reason, adjustments=t.adjustments)
            for t in ledger.trades
        )  # fmt: skip
        session.add_all(
            BacktestPosition(run_id=run_id, pos_date=d, symbol=s, sleeve=sl, weight=w)
            for d, s, sl, w in ledger.positions
        )  # fmt: skip
        session.add_all(
            BacktestDaily(run_id=run_id, day=d, cash_weight=c, timing_factor=f, sleeve_pnl=p)
            for d, c, f, p in ledger.daily
        )  # fmt: skip
    rows = [
        BacktestEquity(
            run_id=run_id,
            equity_date=pd.Timestamp(ts).date(),
            equity=Decimal(repr(float(eq))),
            drawdown=float(dd),
        )
        for ts, eq, dd in zip(equity.index, equity, drawdown.reindex(equity.index), strict=True)
    ]
    session.add_all(rows)
    session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id)
        .values(status="done", metrics=dict(metrics), error=None, finished_at=datetime.now(UTC))
    )


def fail_run(session: Session, run_id: int, error: str) -> None:
    session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id)
        .values(status="failed", error=error[:4000], finished_at=datetime.now(UTC))
    )


def claim_next(session: Session) -> BacktestRun | None:
    """领取最早的 queued 任务并标记为 running。

    SELECT ... FOR UPDATE SKIP LOCKED：多个 worker 并发时，已被其他事务锁定的行直接跳过，
    同一任务不会被重复领取。调用方需在事务中调用，并在返回后提交。
    """
    run = session.scalars(
        select(BacktestRun)
        .where(BacktestRun.status == "queued")
        .order_by(BacktestRun.created_at, BacktestRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if run is None:
        return None
    run.status = "running"
    run.started_at = datetime.now(UTC)
    session.flush()
    return run
