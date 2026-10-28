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
from quant.db.models import BacktestEquity, BacktestRun

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
