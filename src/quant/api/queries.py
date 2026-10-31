"""面板的数据访问：纯查询，Session / ParquetStore 进，接口模型出。

除 quant.api.backtests 的提交外全部只读；本模块不导入 broker。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant.api.schemas import (
    MODES,
    AccountPoint,
    BacktestRunOut,
    Bar,
    DataStatusRow,
    EquityPoint,
    FailureOut,
    FillOut,
    JobStatusOut,
    Mode,
    OrderOut,
    Position,
    QueueStatus,
    SignalOut,
)
from quant.core.calendar import TradingCalendar
from quant.data.store import ParquetStore
from quant.data.validate import validate_bars
from quant.db.models import (
    AccountSnapshotRow,
    BacktestEquity,
    BacktestRun,
    JobRun,
    Order,
    PositionSnapshot,
    Signal,
    SystemFlag,
)
from quant.db.models import Fill as FillRow

VALIDATION_WINDOW_DAYS = 365


def _first_line(text: str | None) -> str:
    return text.splitlines()[0] if text else ""


def _opt(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


# ---- 账户 ----


def accounts(s: Session) -> dict[Mode, bool]:
    """每个账户模式是否有任何数据（快照或订单）。"""
    out: dict[Mode, bool] = {}
    for m in MODES:
        has = any(
            s.scalar(select(func.count()).select_from(t).where(t.mode == m)) or 0
            for t in (AccountSnapshotRow, PositionSnapshot, Order)
        )
        out[m] = bool(has)
    return out


def positions(s: Session, mode: Mode) -> list[Position]:
    """最近一个快照日的持仓。"""
    last = s.scalars(
        select(PositionSnapshot.snapshot_date)
        .where(PositionSnapshot.mode == mode)
        .order_by(PositionSnapshot.snapshot_date.desc())
        .limit(1)
    ).first()
    if last is None:
        return []
    rows = s.scalars(
        select(PositionSnapshot)
        .where(PositionSnapshot.mode == mode, PositionSnapshot.snapshot_date == last)
        .order_by(PositionSnapshot.market_value.desc())
    ).all()
    return [
        Position(snapshot_date=r.snapshot_date, symbol=r.symbol, qty=r.qty,
                 avg_cost=float(r.avg_cost), market_value=float(r.market_value))
        for r in rows
    ]  # fmt: skip


def account_curve(s: Session, mode: Mode) -> list[AccountPoint]:
    rows = s.scalars(
        select(AccountSnapshotRow)
        .where(AccountSnapshotRow.mode == mode)
        .order_by(AccountSnapshotRow.snapshot_date)
    ).all()
    return [
        AccountPoint(date=r.snapshot_date, cash=float(r.cash),
                     net_liquidation=float(r.net_liquidation))
        for r in rows
    ]  # fmt: skip


def latest_signals(s: Session) -> list[SignalOut]:
    """每个策略最近一次的目标仓位。"""
    rows = s.scalars(select(Signal).order_by(Signal.signal_date.desc()).limit(50)).all()
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.strategy in seen:
            continue
        seen.add(r.strategy)
        out.append(
            SignalOut(strategy=r.strategy, signal_date=r.signal_date, data_as_of=r.data_as_of,
                      target_weights={k: float(v) for k, v in r.target_weights.items()})
        )  # fmt: skip
    return out


def orders(s: Session, mode: Mode, limit: int = 200) -> list[OrderOut]:
    rows = s.scalars(
        select(Order).where(Order.mode == mode).order_by(Order.created_at.desc()).limit(limit)
    ).all()
    return [
        OrderOut(created_at=r.created_at, client_order_id=r.client_order_id, symbol=r.symbol,
                 side=r.side, order_type=r.order_type, qty=r.qty,
                 limit_price=_opt(r.limit_price), reference_price=_opt(r.reference_price),
                 status=r.status, filled_qty=r.filled_qty,
                 avg_fill_price=_opt(r.avg_fill_price), reason=r.reason or "")
        for r in rows
    ]  # fmt: skip


def fills(s: Session, mode: Mode, limit: int = 200) -> list[FillOut]:
    rows = s.scalars(
        select(FillRow).where(FillRow.mode == mode).order_by(FillRow.filled_at.desc()).limit(limit)
    ).all()
    return [
        FillOut(filled_at=r.filled_at, symbol=r.symbol, side=r.side, qty=r.qty,
                price=float(r.price), commission=float(r.commission))
        for r in rows
    ]  # fmt: skip


# ---- 回测 ----


def backtest_runs(s: Session, limit: int = 200) -> list[BacktestRunOut]:
    runs = s.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).all()
    return [
        BacktestRunOut(
            id=r.id, strategy=r.strategy, status=r.status, start=r.start, end=r.end,
            params={k: v for k, v in r.params.items() if not k.startswith("_")},
            costs=r.params.get("_costs"), metrics=r.metrics or {},
            error=_first_line(r.error), created_at=r.created_at, finished_at=r.finished_at,
        )
        for r in runs
    ]  # fmt: skip


def backtest_equity(s: Session, run_ids: Sequence[int]) -> list[EquityPoint]:
    if not run_ids:
        return []
    points = s.scalars(
        select(BacktestEquity)
        .where(BacktestEquity.run_id.in_(run_ids))
        .order_by(BacktestEquity.run_id, BacktestEquity.equity_date)
    ).all()
    return [
        EquityPoint(run_id=p.run_id, date=p.equity_date, equity=float(p.equity),
                    drawdown=float(p.drawdown))
        for p in points
    ]  # fmt: skip


def queue_status(s: Session) -> QueueStatus:
    counts = dict(
        s.execute(select(BacktestRun.status, func.count()).group_by(BacktestRun.status)).all()
    )
    return QueueStatus(**{k: int(v) for k, v in counts.items() if k in QueueStatus.model_fields})


# ---- 系统 ----


def job_status(s: Session) -> list[JobStatusOut]:
    """每个任务最近一次运行。"""
    rows = s.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(500)).all()
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.job_name in seen:
            continue
        seen.add(r.job_name)
        out.append(
            JobStatusOut(job_name=r.job_name, trade_date=r.trade_date, status=r.status,
                         started_at=r.started_at, finished_at=r.finished_at,
                         error=_first_line(r.error))
        )  # fmt: skip
    return out


def recent_failures(s: Session, limit: int = 20) -> list[FailureOut]:
    """最近的失败（任务失败、回测失败），新的在前。"""
    jobs = s.scalars(
        select(JobRun)
        .where(JobRun.status == "failed")
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    ).all()
    bts = s.scalars(
        select(BacktestRun)
        .where(BacktestRun.status == "failed")
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
    ).all()
    out = [
        FailureOut(time=j.finished_at or j.started_at, source=f"任务 {j.job_name}",
                   message=_first_line(j.error))
        for j in jobs
    ] + [
        FailureOut(time=b.finished_at or b.created_at, source=f"回测 #{b.id}",
                   message=_first_line(b.error))
        for b in bts
    ]  # fmt: skip
    return sorted(out, key=lambda f: f.time, reverse=True)[:limit]


def flags(s: Session) -> dict[str, str]:
    """系统开关（只读显示），如 trading_enabled。"""
    return {f.key: f.value for f in s.scalars(select(SystemFlag)).all()}


def last_job_finished(s: Session) -> datetime | None:
    """最近一次成功任务的完成时间，用作“数据截至”。"""
    return s.scalar(select(func.max(JobRun.finished_at)).where(JobRun.status == "success"))


# ---- 行情 ----


def data_status(store: ParquetStore, cal: TradingCalendar, expected: date) -> list[DataStatusRow]:
    """每个标的：最新日期、行数、来源、最近一年的校验结果。"""
    out = []
    for sym in store.symbols():
        df = store.read_bars(sym)
        info = store.ingestion_info(sym)
        last = pd.Timestamp(df.index[-1]).date() if len(df) else None
        start = max(
            expected - timedelta(days=VALIDATION_WINDOW_DAYS),
            pd.Timestamp(df.index[0]).date() if len(df) else expected,
        )
        report = validate_bars(sym, df, cal, start, expected)
        out.append(
            DataStatusRow(
                symbol=sym, last_date=last, rows=len(df),
                sources=", ".join(sorted(info["source"].unique())) if len(info) else "",
                last_ingested=pd.Timestamp(info["ingested_at"].max()).to_pydatetime()
                if len(info) else None,
                ok=report.ok, issues="" if report.ok else report.summary(limit=5),
            )
        )  # fmt: skip
    return out


def bars(store: ParquetStore, symbol: str, start: date | None) -> list[Bar]:
    df = store.read_bars(symbol, start)
    return [
        Bar(date=d.date(), open=float(o), high=float(h), low=float(lo), close=float(c),
            adj_close=float(a), volume=float(v))
        for d, o, h, lo, c, a, v in zip(
            pd.DatetimeIndex(df.index), df["open"], df["high"], df["low"], df["close"],
            df["adj_close"], df["volume"], strict=True,
        )
    ]  # fmt: skip
