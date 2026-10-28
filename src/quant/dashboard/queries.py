"""面板的数据访问。除 submit_backtest 外全部只读（PROJECT_PLAN 第 10 章）。

面板不得包含任何下单、撤单、修改熔断开关的功能；本模块也不导入 broker。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from quant.core.calendar import get_calendar
from quant.core.settings import Settings, load_settings
from quant.data.store import ParquetStore
from quant.data.validate import validate_bars
from quant.db.backtests import create_run, make_engine
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


@st.cache_resource
def settings() -> Settings:
    return load_settings()


@st.cache_resource
def engine() -> Engine | None:
    s = settings()
    return make_engine(s.database_url.get_secret_value()) if s.database_url else None


def store() -> ParquetStore:
    return ParquetStore(settings().data_dir)


# ---- 行情 ----


@st.cache_data(ttl=300)
def data_status() -> pd.DataFrame:
    """每个标的：最新日期、行数、来源、最近一年的校验结果。"""
    st_ = store()
    cal = get_calendar()
    expected = cal.last_completed_trading_day(datetime.now(UTC))
    rows = []
    for sym in st_.symbols():
        bars = st_.read_bars(sym)
        info = st_.ingestion_info(sym)
        last = pd.Timestamp(bars.index[-1]).date() if len(bars) else None
        start = max(
            expected - timedelta(days=VALIDATION_WINDOW_DAYS),
            pd.Timestamp(bars.index[0]).date() if len(bars) else expected,
        )
        report = validate_bars(sym, bars, cal, start, expected)
        rows.append(
            {
                "标的": sym,
                "最新日期": last,
                "行数": len(bars),
                "来源": ", ".join(sorted(info["source"].unique())) if len(info) else "",
                "最近写入": info["ingested_at"].max() if len(info) else None,
                "校验": "通过" if report.ok else f"{len(report.issues)} 个问题",
                "问题": report.summary(limit=5) if not report.ok else "",
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def bars(symbol: str, start: date | None = None) -> pd.DataFrame:
    return store().read_bars(symbol, start)


# ---- 回测 ----


def submit_backtest(strategy: str, params: Mapping[str, Any], start: date, end: date) -> int:
    """面板唯一的写操作：插入一条 queued 回测任务，由 backtest-worker 执行。"""
    eng = engine()
    if eng is None:
        raise RuntimeError("未配置数据库，无法提交回测")
    with Session(eng) as s, s.begin():
        run_id = create_run(s, strategy, params, start, end, "queued")
    backtest_runs.clear()
    return run_id


@st.cache_data(ttl=10)
def backtest_runs(limit: int = 200) -> pd.DataFrame:
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        runs = s.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).all()
        return pd.DataFrame(
            [
                {
                    "id": r.id,
                    "策略": r.strategy,
                    "状态": r.status,
                    "开始": r.start,
                    "结束": r.end,
                    "参数": {k: v for k, v in r.params.items() if not k.startswith("_")},
                    "成本": r.params.get("_costs"),
                    "metrics": r.metrics or {},
                    "错误": (r.error or "").splitlines()[0] if r.error else "",
                    "提交时间": r.created_at,
                    "完成时间": r.finished_at,
                }
                for r in runs
            ]
        )


@st.cache_data(ttl=60)
def backtest_equity(run_ids: tuple[int, ...]) -> pd.DataFrame:
    """长表：run_id, date, equity, drawdown。"""
    eng = engine()
    if eng is None or not run_ids:
        return pd.DataFrame(columns=["run_id", "date", "equity", "drawdown"])
    with Session(eng) as s:
        points = s.scalars(
            select(BacktestEquity)
            .where(BacktestEquity.run_id.in_(run_ids))
            .order_by(BacktestEquity.run_id, BacktestEquity.equity_date)
        ).all()
        rows = [(p.run_id, p.equity_date, p.equity, p.drawdown) for p in points]
    df = pd.DataFrame(rows, columns=["run_id", "date", "equity", "drawdown"])
    df["equity"] = df["equity"].astype(float)
    return df


# ---- 交易（只读） ----


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


@st.cache_data(ttl=30)
def positions(mode: str) -> pd.DataFrame:
    """最近一个快照日的持仓。"""
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        last = s.scalars(
            select(PositionSnapshot.snapshot_date)
            .where(PositionSnapshot.mode == mode)
            .order_by(PositionSnapshot.snapshot_date.desc())
            .limit(1)
        ).first()
        if last is None:
            return pd.DataFrame()
        rows = s.scalars(
            select(PositionSnapshot).where(
                PositionSnapshot.mode == mode, PositionSnapshot.snapshot_date == last
            )
        ).all()
        return _frame(
            [
                {"日期": r.snapshot_date, "标的": r.symbol, "股数": r.qty,
                 "成本": float(r.avg_cost), "市值": float(r.market_value)}
                for r in rows
            ]
        )  # fmt: skip


@st.cache_data(ttl=30)
def account_curve(mode: str) -> pd.DataFrame:
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        rows = s.scalars(
            select(AccountSnapshotRow)
            .where(AccountSnapshotRow.mode == mode)
            .order_by(AccountSnapshotRow.snapshot_date)
        ).all()
        return _frame(
            [
                {"日期": r.snapshot_date, "现金": float(r.cash), "净值": float(r.net_liquidation)}
                for r in rows
            ]
        )


@st.cache_data(ttl=30)
def latest_signal() -> pd.DataFrame:
    """每个策略最近一次的目标仓位。"""
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        rows = s.scalars(select(Signal).order_by(Signal.signal_date.desc()).limit(50)).all()
        seen: set[str] = set()
        out = []
        for r in rows:
            if r.strategy in seen:
                continue
            seen.add(r.strategy)
            out.append(
                {"策略": r.strategy, "信号日": r.signal_date, "数据截至": r.data_as_of,
                 "目标权重": dict(r.target_weights)}
            )  # fmt: skip
        return _frame(out)


@st.cache_data(ttl=30)
def orders(mode: str, limit: int = 200) -> pd.DataFrame:
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        rows = s.scalars(
            select(Order).where(Order.mode == mode).order_by(Order.created_at.desc()).limit(limit)
        ).all()
        return _frame(
            [
                {"提交时间": r.created_at, "client_order_id": r.client_order_id,
                 "标的": r.symbol, "方向": r.side, "类型": r.order_type, "数量": r.qty,
                 "限价": None if r.limit_price is None else float(r.limit_price),
                 "参考价": None if r.reference_price is None else float(r.reference_price),
                 "状态": r.status, "成交": r.filled_qty,
                 "成交均价": None if r.avg_fill_price is None else float(r.avg_fill_price),
                 "原因": r.reason or ""}
                for r in rows
            ]
        )  # fmt: skip


@st.cache_data(ttl=30)
def fills(mode: str, limit: int = 200) -> pd.DataFrame:
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        rows = s.scalars(
            select(FillRow)
            .where(FillRow.mode == mode)
            .order_by(FillRow.filled_at.desc())
            .limit(limit)
        ).all()
        return _frame(
            [
                {"成交时间": r.filled_at, "标的": r.symbol, "方向": r.side, "数量": r.qty,
                 "价格": float(r.price), "佣金": float(r.commission)}
                for r in rows
            ]
        )  # fmt: skip


# ---- 系统（只读） ----


@st.cache_data(ttl=15)
def job_status() -> pd.DataFrame:
    """每个任务最近一次运行。"""
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
        rows = s.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(500)).all()
        seen: set[str] = set()
        out = []
        for r in rows:
            if r.job_name in seen:
                continue
            seen.add(r.job_name)
            out.append(
                {"任务": r.job_name, "交易日": r.trade_date, "状态": r.status,
                 "开始": r.started_at, "结束": r.finished_at,
                 "错误": (r.error or "").splitlines()[0] if r.error else ""}
            )  # fmt: skip
        return _frame(out)


@st.cache_data(ttl=15)
def recent_failures(limit: int = 20) -> pd.DataFrame:
    """最近的失败（任务失败、回测失败）。告警渠道（#33）完成前以此代替“最近告警”。"""
    eng = engine()
    if eng is None:
        return pd.DataFrame()
    with Session(eng) as s:
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
            {"时间": j.finished_at or j.started_at, "来源": f"任务 {j.job_name}",
             "内容": (j.error or "").splitlines()[0] if j.error else ""}
            for j in jobs
        ] + [
            {"时间": b.finished_at or b.created_at, "来源": f"回测 #{b.id}",
             "内容": (b.error or "").splitlines()[0] if b.error else ""}
            for b in bts
        ]  # fmt: skip
        df = _frame(out)
        return df.sort_values("时间", ascending=False).head(limit) if len(df) else df


@st.cache_data(ttl=15)
def flags() -> dict[str, str]:
    """系统开关（只读显示），如 trading_enabled。"""
    eng = engine()
    if eng is None:
        return {}
    with Session(eng) as s:
        return {f.key: f.value for f in s.scalars(select(SystemFlag)).all()}


@st.cache_data(ttl=15)
def queue_status() -> dict[str, int]:
    runs = backtest_runs()
    if runs.empty:
        return {}
    return {str(k): int(v) for k, v in runs["状态"].value_counts().items()}
