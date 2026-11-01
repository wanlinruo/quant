"""回测页的分析视图（#67 A 批）：读回测记录与净值，以及与回测同一数据源的 QQQ / SPY。只读。"""

from __future__ import annotations

import json
import shlex
from bisect import bisect_left
from dataclasses import asdict
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api import backtest_analysis as ba
from quant.api.backtests import FORM_STRATEGY
from quant.api.schemas import (
    BacktestAnalysis,
    BacktestPositions,
    BacktestTrades,
    BtDailyPosOut,
    BtHoldingOut,
    BtLongestOut,
    BtOverviewOut,
    BtParamOut,
    BtReasonOut,
    BtReproOut,
    BtReturnsOut,
    BtRiskOut,
    BtSeriesOut,
    BtSleevesOut,
    BtStatOut,
    BtSymbolOut,
    BtTimingOut,
    BtTradeOut,
    BtTradeRef,
    BtTradesSummary,
)
from quant.core.settings import Settings
from quant.db.models import (
    BacktestDaily,
    BacktestEquity,
    BacktestPosition,
    BacktestRun,
    BacktestTrade,
)
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
        text = json.dumps(v, separators=(",", ":"))  # 与 CLI 的 json.loads 往返一致
        parts.append(f"--param {shlex.quote(f'{k}={text}')}")
    if CASH_KEY in run.params:
        parts.append(f"--cash {run.params[CASH_KEY]}")
    costs = run.params.get(COSTS_KEY) or {}
    for k in CLI_COSTS:  # CLI 只接受这三项；平台费、封顶等取当前配置
        if costs.get(k) is not None:
            parts.append(f"--{k.replace('_', '-')} {costs[k]}")
    parts.append("--no-db")
    return " ".join(parts)


CLI_COSTS = ("commission_per_share", "min_commission", "slippage")


def _norm(s: pd.Series, index: pd.DatetimeIndex) -> list[float | None]:
    """归一化到自身第一个有效值；只在基准自身日期范围内向前填充。"""
    s = s.dropna().sort_index()
    x = s.reindex(s.index.union(index)).ffill().reindex(index)
    x[(x.index < s.index[0]) | (x.index > s.index[-1])] = float("nan")
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
    if qqq is not None and not ba.covers(eq, qqq):
        b = qqq.dropna()
        span = f"{b.index[0].date()} – {b.index[-1].date()}" if len(b) else ""
        out["bench_note"] = (
            f"QQQ 数据只覆盖 {span}，与回测区间不一致，未作对照" if span else "没有 QQQ 数据"
        )
        qn_partial = _norm(qqq, idx) if len(b) else None
        qqq = None
    else:
        qn_partial = None
    qn = _norm(qqq, idx) if qqq is not None else qn_partial
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
    stats = [BtStatOut(**asdict(x)) for x in ba.stat_bar(eq, qqq)]
    tv = trades_view(s, run_id)
    if tv is not None and tv.summary is not None:
        fill = {"win_rate": tv.summary.win_rate, "profit_factor": tv.summary.profit_factor,
                "trades": float(tv.summary.trades)}  # fmt: skip
        stats = [x.model_copy(update={"value": fill[x.key]}) if x.key in fill else x for x in stats]
    out["stats"] = stats
    daily = s.scalars(select(BacktestDaily).where(BacktestDaily.run_id == run_id)
                      .order_by(BacktestDaily.day)).all()  # fmt: skip
    sleeves = _sleeve_series(list(daily), out["initial_cash"]) if daily else None
    split = ba.split_metrics(eq, qqq)
    out["overview"] = BtOverviewOut(checks=ba.checks(split), split=split, yearly=ba.yearly(eq, qqq))
    out["returns"] = BtReturnsOut(
        monthly=ba.monthly_table(eq, qqq), distribution=ba.distribution(eq),
        best_worst=ba.best_worst(eq), rolling=ba.rolling_12m(eq), sleeves=sleeves,
    )  # fmt: skip
    out["risk"] = BtRiskOut(
        episodes=ba.drawdown_episodes(eq, qqq), capture=ba.capture(eq, qqq),
        table=ba.risk_table(eq, qqq), rolling_beta=ba.rolling_beta(eq, qqq), tail=ba.tail(eq),
        timing=timing_periods(list(daily), eq) if daily else [],
    )  # fmt: skip
    return BacktestAnalysis(**out)


def _same(v: Any) -> Any:
    return json.dumps(list(v) if isinstance(v, tuple) else v, sort_keys=True, default=str)


# ---- 交易明细与持仓变化（#67 B 批）----

OLD_RUN_NOTE = "此回测运行于交易记录功能上线之前，重新运行（复制参数新建）即可查看"


def _dates(s: Session, run_id: int) -> list[date]:
    return list(s.scalars(select(BacktestEquity.equity_date).where(BacktestEquity.run_id == run_id)
                          .order_by(BacktestEquity.equity_date)))  # fmt: skip


def _hold_days(dates: list[date], start: date, end: date | None) -> int:
    i = bisect_left(dates, start)
    j = bisect_left(dates, end) if end is not None else len(dates) - 1
    return max(j - i, 0)


def _rate(xs: list[float]) -> float | None:
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


def trades_view(s: Session, run_id: int) -> BacktestTrades | None:
    if s.get(BacktestRun, run_id) is None:
        return None
    rows = s.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run_id)
                     .order_by(BacktestTrade.seq)).all()  # fmt: skip
    if not rows:
        return BacktestTrades(note=OLD_RUN_NOTE)
    dates = _dates(s, run_id)
    out = [BtTradeOut(seq=t.seq, symbol=t.symbol, sleeve=t.sleeve, open_date=t.open_date,
                      open_price=t.open_price, close_date=t.close_date, close_price=t.close_price,
                      qty=t.qty, ret=t.ret, pnl=t.pnl, exit_reason=t.exit_reason,
                      days=_hold_days(dates, t.open_date, t.close_date), adjustments=t.adjustments)
           for t in rows]  # fmt: skip
    closed = [t for t in out if t.close_date is not None]
    wins = sum(t.pnl for t in closed if t.pnl > 0)
    losses = -sum(t.pnl for t in closed if t.pnl < 0)
    rets = [t.ret for t in closed if t.ret is not None]
    best = max(closed, key=lambda t: t.pnl, default=None)
    worst = min(closed, key=lambda t: t.pnl, default=None)
    summary = BtTradesSummary(
        trades=len(out), closed=len(closed), win_rate=_rate([t.pnl for t in closed]),
        profit_factor=wins / losses if losses else None,
        avg_days=sum(t.days for t in closed) / len(closed) if closed else None,
        avg_ret=sum(rets) / len(rets) if rets else None,
        best=BtTradeRef(symbol=best.symbol, pnl=best.pnl, ret=best.ret) if best else None,
        worst=BtTradeRef(symbol=worst.symbol, pnl=worst.pnl, ret=worst.ret) if worst else None,
    )  # fmt: skip
    by_reason: dict[str, list[BtTradeOut]] = {}
    for t in closed:
        by_reason.setdefault(t.exit_reason or "—", []).append(t)
    reasons = [BtReasonOut(reason=k, count=len(v), win_rate=_rate([t.pnl for t in v]),
                           avg_ret=_mean([t.ret for t in v]), pnl=sum(t.pnl for t in v))
               for k, v in by_reason.items()]  # fmt: skip
    by_sym: dict[str, list[BtTradeOut]] = {}
    for t in out:
        by_sym.setdefault(t.symbol, []).append(t)
    symbols = [BtSymbolOut(symbol=k, trades=len(v), pnl=sum(t.pnl for t in v),
                           win_rate=_rate([t.pnl for t in v if t.close_date is not None]),
                           days=sum(t.days for t in v))
               for k, v in by_sym.items()]  # fmt: skip
    return BacktestTrades(summary=summary, trades=out,
                          by_reason=sorted(reasons, key=lambda r: -r.count),
                          by_symbol=sorted(symbols, key=lambda r: -r.pnl))  # fmt: skip


def _mean(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def timing_periods(daily: list[BacktestDaily], eq: pd.Series | None) -> list[BtTimingOut]:
    """择时系数 < 1 的连续时段。估算的“系数为 1 时收益”= 期间收益 ÷ 平均系数（近似）。"""
    out: list[BtTimingOut] = []
    run: list[BacktestDaily] = []

    def flush() -> None:
        if not run:
            return
        start, end = run[0].day, run[-1].day
        factor = sum(r.timing_factor or 1.0 for r in run) / len(run)
        ret = est = None
        if eq is not None:
            seg = eq[(eq.index >= pd.Timestamp(start)) & (eq.index <= pd.Timestamp(end))]
            before = eq[eq.index < pd.Timestamp(start)]
            base = before.iloc[-1] if len(before) else (seg.iloc[0] if len(seg) else None)
            if base is not None and len(seg):
                ret = float(seg.iloc[-1] / base - 1)
                est = ret / factor if factor else None
        saved = ret - est if ret is not None and est is not None else None
        out.append(BtTimingOut(start=start, end=end, days=len(run), factor=factor, ret=ret,
                               est_full=est, saved=saved))  # fmt: skip
        run.clear()

    for r in daily:
        if r.timing_factor is not None and r.timing_factor < 1:
            run.append(r)
        else:
            flush()
    flush()
    return out


def positions_view(s: Session, run_id: int, day: date | None) -> BacktestPositions | None:
    if s.get(BacktestRun, run_id) is None:
        return None
    daily = s.scalars(select(BacktestDaily).where(BacktestDaily.run_id == run_id)
                      .order_by(BacktestDaily.day)).all()  # fmt: skip
    if not daily:
        return BacktestPositions(note=OLD_RUN_NOTE)
    sleeves: dict[date, dict[str, float]] = {}
    for p in s.scalars(select(BacktestPosition).where(BacktestPosition.run_id == run_id)):
        d = sleeves.setdefault(p.pos_date, {})
        d[p.sleeve] = d.get(p.sleeve, 0.0) + p.weight
    rows = [BtDailyPosOut(date=r.day, cash=r.cash_weight, sleeves=sleeves.get(r.day, {}),
                          timing_factor=r.timing_factor) for r in daily]  # fmt: skip
    eq = _equity(s, run_id)
    pick = day or daily[-1].day
    held = s.scalars(select(BacktestPosition).where(BacktestPosition.run_id == run_id,
                                                    BacktestPosition.pos_date == pick)
                     .order_by(BacktestPosition.weight.desc())).all()  # fmt: skip
    trades = trades_view(s, run_id)
    longest = sorted(trades.trades if trades else [], key=lambda t: -t.days)[:10]
    return BacktestPositions(
        daily=rows, timing=timing_periods(list(daily), eq), day=pick,
        holdings=[BtHoldingOut(symbol=p.symbol, sleeve=p.sleeve, weight=p.weight) for p in held],
        longest=[BtLongestOut(symbol=t.symbol, sleeve=t.sleeve, days=t.days,
                              open_date=t.open_date, close_date=t.close_date) for t in longest],
    )  # fmt: skip


def _equity(s: Session, run_id: int) -> pd.Series | None:
    pts = s.scalars(select(BacktestEquity).where(BacktestEquity.run_id == run_id)
                    .order_by(BacktestEquity.equity_date)).all()  # fmt: skip
    if len(pts) < 2:
        return None
    idx = pd.DatetimeIndex([pd.Timestamp(p.equity_date) for p in pts])
    return pd.Series([float(p.equity) for p in pts], index=idx)


def _sleeve_series(daily: list[BacktestDaily], cash: float | None) -> BtSleevesOut | None:
    """各子仓累计盈亏 ÷ 初始资金。"""
    if not cash:
        return None
    names = sorted({k for r in daily for k in r.sleeve_pnl})
    acc = dict.fromkeys(names, 0.0)
    series: dict[str, list[float]] = {k: [] for k in names}
    for r in daily:
        for k in names:
            acc[k] += float(r.sleeve_pnl.get(k, 0.0))
            series[k].append(acc[k] / cash)
    return BtSleevesOut(dates=[r.day for r in daily], series=series)
