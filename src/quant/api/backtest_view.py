"""回测页的分析视图（#67 A 批）：读回测记录与净值，以及与回测同一数据源的 QQQ / SPY。只读。"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api import backtest_analysis as ba
from quant.api.backtests import FORM_STRATEGY
from quant.api.schemas import (
    BacktestAnalysis,
    BtOverviewOut,
    BtParamOut,
    BtReproOut,
    BtReturnsOut,
    BtRiskOut,
    BtSeriesOut,
    BtStatOut,
)
from quant.core.settings import Settings
from quant.db.models import BacktestEquity, BacktestRun
from quant.engine.evaluation import OOS_START
from quant.engine.runner import CASH_KEY, COSTS_KEY
from quant.strategies.etf_momentum import MomentumParams


def _bench(settings: Settings, symbol: str) -> pd.Series | None:
    """与回测同一数据源的复权收盘价；没有时为 None。"""
    from quant.data.backtest_data import backtest_store
    from quant.data.store import ParquetStore

    data_dir = settings.data_dir
    tiingo = settings.data.backtest_source == "tiingo"
    store = backtest_store(data_dir) if tiingo else ParquetStore(data_dir)
    try:
        df = store.read_bars(symbol)
    except (OSError, ValueError):
        return None
    if df.empty:
        return None
    col = "adj_close" if "adj_close" in df.columns else "close"
    return df[col].astype(float)


def _defaults(strategy: str, settings: Settings) -> dict[str, Any]:
    """“默认”参数：与提交回测时的来源相同。"""
    if strategy == FORM_STRATEGY:
        return {
            k: list(v) if isinstance(v, tuple) else v for k, v in asdict(MomentumParams()).items()
        }
    if settings.strategy.name == strategy:
        return dict(settings.strategy.params)
    return {}


def _command(run: BacktestRun) -> str:
    parts = ["uv run quant backtest", f"--strategy {run.strategy}", f"--start {run.start}",
             f"--end {run.end}"]  # fmt: skip
    for k, v in run.params.items():
        if k.startswith("_"):
            continue
        text = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
        parts.append(f"--param {shlex.quote(f'{k}={text}')}")
    if CASH_KEY in run.params:
        parts.append(f"--cash {run.params[CASH_KEY]}")
    for k, v in (run.params.get(COSTS_KEY) or {}).items():
        parts.append(f"--{k.replace('_', '-')} {v}")
    parts.append("--no-db")
    return " ".join(parts)


def _norm(s: pd.Series, index: pd.DatetimeIndex) -> list[float | None]:
    x = s.reindex(s.index.union(index)).ffill().reindex(index)
    first = x.dropna()
    base = float(first.iloc[0]) if len(first) else 1.0
    return [None if pd.isna(v) else float(v) / base for v in x.to_numpy()]


def analysis(s: Session, run_id: int, settings: Settings) -> BacktestAnalysis | None:
    run = s.get(BacktestRun, run_id)
    if run is None:
        return None
    cash = run.params.get(CASH_KEY)
    dur = (
        (run.finished_at - run.started_at).total_seconds()
        if run.finished_at and run.started_at
        else None
    )
    out: dict[str, Any] = {
        "id": run.id, "strategy": run.strategy, "status": run.status, "start": run.start,
        "end": run.end, "error": run.error, "initial_cash": float(cash) if cash else None,
        "duration_s": dur, "oos_start": OOS_START,
    }  # fmt: skip
    defaults = _defaults(run.strategy, settings)
    out["params"] = [
        BtParamOut(key=k, value=v, default=defaults.get(k),
                   changed=k in defaults and _same(v) != _same(defaults[k]))
        for k, v in run.params.items() if not k.startswith("_")
    ]  # fmt: skip
    data = (run.metrics or {}).get("data") or {}
    out["repro"] = BtReproOut(source=data.get("source"), data_end=data.get("data_end"),
                              coverage=data.get("coverage"), command=_command(run))  # fmt: skip
    points = s.scalars(select(BacktestEquity).where(BacktestEquity.run_id == run_id)
                       .order_by(BacktestEquity.equity_date)).all()  # fmt: skip
    if run.status != "done" or len(points) < 2:
        return BacktestAnalysis(**out)
    idx = pd.DatetimeIndex([pd.Timestamp(p.equity_date) for p in points])
    eq = pd.Series([float(p.equity) for p in points], index=idx)
    if out["initial_cash"] is None:
        out["initial_cash"] = float(eq.iloc[0])
    qqq, spy = _bench(settings, "QQQ"), _bench(settings, "SPY")
    qn = _norm(qqq, idx) if qqq is not None else None
    if qn is not None and all(v is None for v in qn):
        qn = qqq = None
    sn = _norm(spy, idx) if spy is not None else None
    qdd = None
    if qn is not None:
        qs = pd.Series([float("nan") if v is None else v for v in qn], index=idx)
        qdd = [None if pd.isna(v) else float(v) for v in (qs / qs.cummax() - 1).to_numpy()]
    out["series"] = BtSeriesOut(dates=[d.date() for d in idx], strategy=list(eq / eq.iloc[0]),
                                qqq=qn, spy=sn, drawdown=[float(p.drawdown) for p in points],
                                qqq_drawdown=qdd)  # fmt: skip
    out["stats"] = [BtStatOut(**asdict(x)) for x in ba.stat_bar(eq, qqq)]
    split = ba.split_metrics(eq, qqq)
    out["overview"] = BtOverviewOut(checks=ba.checks(split), split=split, yearly=ba.yearly(eq, qqq))
    out["returns"] = BtReturnsOut(
        monthly=ba.monthly_table(eq, qqq), distribution=ba.distribution(eq),
        best_worst=ba.best_worst(eq), rolling=ba.rolling_12m(eq),
    )  # fmt: skip
    out["risk"] = BtRiskOut(
        episodes=ba.drawdown_episodes(eq, qqq), capture=ba.capture(eq, qqq),
        table=ba.risk_table(eq, qqq), rolling_beta=ba.rolling_beta(eq, qqq), tail=ba.tail(eq),
    )  # fmt: skip
    return BacktestAnalysis(**out)


def _same(v: Any) -> Any:
    return json.dumps(list(v) if isinstance(v, tuple) else v, sort_keys=True, default=str)
