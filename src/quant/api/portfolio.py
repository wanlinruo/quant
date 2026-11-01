"""组合页的数据：按账户 mode 读取快照、信号、订单成交，结合本地日线计算（只读，#65）。

信号明细 detail 的结构见 quant.strategies.combo.Combo.run_explained；没有明细时相关字段为空。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api import analytics as an
from quant.api.schemas import (
    CalendarDayOut,
    ClosedTradeOut,
    ClosedTradesOut,
    ContributionRow,
    HoldingRow,
    Mode,
    PerformanceOut,
    PerfPoint,
    PortfolioOverview,
    PortfolioSummaryOut,
    PreviewRow,
    RebalancePreview,
    ReconcileOut,
    RiskOut,
    SleeveAlloc,
    TimingOut,
    TradeStatsOut,
)
from quant.core.settings import CostSettings
from quant.data.store import ParquetStore, is_valid_symbol
from quant.db.models import AccountSnapshotRow, JobRun, Order, PositionSnapshot, Signal, SystemFlag
from quant.db.models import Fill as FillRow
from quant.strategies import STRATEGIES, get_strategy
from quant.strategies.events import EventCalendar

BENCHMARKS = ("QQQ", "SPY")
DEFAULT_MIN_DIFF = 0.01
EARNINGS_AHEAD_DAYS = 7  # “下周有财报”的范围（自然日）
MA_EXIT = 10


@dataclass(frozen=True)
class Stores:
    """主数据目录（含分红复权）与候选池目录（未复权）。"""

    main: ParquetStore
    universe: ParquetStore

    def bars(self, symbol: str) -> pd.DataFrame:
        if not is_valid_symbol(symbol):
            return pd.DataFrame()
        df = self.main.read_bars(symbol)
        return df if len(df) else self.universe.read_bars(symbol)


# ---- 基础读取 ----


def nav_series(s: Session, mode: Mode) -> pd.Series:
    rows = s.scalars(
        select(AccountSnapshotRow)
        .where(AccountSnapshotRow.mode == mode)
        .order_by(AccountSnapshotRow.snapshot_date)
    ).all()
    return pd.Series(
        [float(r.net_liquidation) for r in rows],
        index=pd.DatetimeIndex([pd.Timestamp(r.snapshot_date) for r in rows]),
        dtype=float,
    )


def last_account(s: Session, mode: Mode) -> AccountSnapshotRow | None:
    return s.scalars(
        select(AccountSnapshotRow)
        .where(AccountSnapshotRow.mode == mode)
        .order_by(AccountSnapshotRow.snapshot_date.desc())
        .limit(1)
    ).first()


def snapshots_of(s: Session, mode: Mode) -> list[PositionSnapshot]:
    return list(
        s.scalars(
            select(PositionSnapshot)
            .where(PositionSnapshot.mode == mode)
            .order_by(PositionSnapshot.snapshot_date)
        ).all()
    )


def latest_positions(snaps: Sequence[PositionSnapshot]) -> list[PositionSnapshot]:
    if not snaps:
        return []
    last = snaps[-1].snapshot_date
    return sorted(
        (r for r in snaps if r.snapshot_date == last), key=lambda r: -float(r.market_value)
    )


def held_by_day(snaps: Sequence[PositionSnapshot]) -> dict[date, set[str]]:
    out: dict[date, set[str]] = {}
    for r in snaps:
        out.setdefault(r.snapshot_date, set())
        if r.qty:
            out[r.snapshot_date].add(r.symbol)
    return out


def signals_of(s: Session, mode: Mode, limit: int | None = None) -> list[Signal]:
    q = select(Signal).where(Signal.mode == mode).order_by(Signal.signal_date.desc())
    return list(s.scalars(q.limit(limit) if limit else q).all())


def _close(stores: Stores, symbol: str, adjusted: bool = False) -> pd.Series:
    df = stores.bars(symbol)
    if not len(df):
        return pd.Series(dtype=float)
    col = "adj_close" if adjusted and df["adj_close"].notna().any() else "close"
    return df[col].astype(float)


# ---- 信号明细的读取 ----


@dataclass(frozen=True)
class Detail:
    raw: Mapping[str, Any] | None

    def sleeve_of(self, symbol: str, target: Mapping[str, float]) -> str | None:
        if self.raw is None:
            return None
        sleeves = self.raw.get("sleeves", {})
        m, w = symbol in sleeves.get("momentum", {}), symbol in sleeves.get("swing", {})
        if m and w:
            return "both"
        if m:
            return "momentum"
        if w:
            return "swing"
        return "exit" if symbol not in target else None

    def rank(self, symbol: str) -> int | None:
        if self.raw is None:
            return None
        ranks: list[str] = self.raw.get("momentum_rank", [])
        return ranks.index(symbol) + 1 if symbol in ranks else None

    @property
    def top_n(self) -> int:
        return int(self.raw.get("top_n", 10)) if self.raw else 10

    @property
    def factor(self) -> float | None:
        return (
            float(self.raw["timing_factor"]) if self.raw and "timing_factor" in self.raw else None
        )

    def sleeve_target(self, sleeve: str) -> float | None:
        if self.raw is None:
            return None
        weights = self.raw.get("sleeves", {}).get(sleeve, {})
        k = float(self.raw.get("sleeve_weights", {}).get(sleeve, 0.0))
        return float(sum(weights.values())) * k * (self.factor or 1.0)

    def sleeve_share(self, symbol: str) -> dict[str, float]:
        """symbol 的目标权重在两仓之间的比例（用于按子仓拆分）。"""
        if self.raw is None:
            return {}
        parts = {
            sl: float(self.raw["sleeves"].get(sl, {}).get(symbol, 0.0))
            * float(self.raw.get("sleeve_weights", {}).get(sl, 0.0))
            for sl in ("momentum", "swing")
        }
        total = sum(parts.values())
        return {k: v / total for k, v in parts.items() if v} if total else {}


def detail_of(sig: Signal | None) -> Detail:
    return Detail(sig.detail if sig is not None else None)


# ---- 概览 ----


def _bench_on(stores: Stores, symbol: str, idx: pd.Index) -> pd.Series:
    close = _close(stores, symbol, adjusted=True)
    if not len(close) or not len(idx):
        return pd.Series(index=idx, dtype=float)
    return close.reindex(close.index.union(idx)).ffill().reindex(idx)


def overview(s: Session, mode: Mode, stores: Stores, events: EventCalendar) -> PortfolioOverview:
    nav = nav_series(s, mode)
    acct = last_account(s, mode)
    snaps = snapshots_of(s, mode)
    held = latest_positions(snaps)
    sigs = signals_of(s, mode, 1)
    sig = sigs[0] if sigs else None
    det = detail_of(sig)
    target = dict(sig.target_weights) if sig else {}
    summ = an.summary(nav, _bench_on(stores, "QQQ", nav.index), _bench_on(stores, "SPY", nav.index))
    risk = an.risk(nav, _bench_on(stores, "SPY", nav.index), _bench_on(stores, "QQQ", nav.index))
    netliq = float(acct.net_liquidation) if acct else None
    weights = {r.symbol: float(r.market_value) / netliq for r in held} if netliq else {}
    top5, top1 = an.concentration(weights) if weights else (None, None)
    timing = None
    if det.raw is not None and det.factor is not None:
        timing = TimingOut(
            factor=det.factor,
            vol_symbol=str(det.raw.get("vol_symbol", "QQQ")),
            vol_20d=det.raw.get("vol_20d"),
            threshold=float(det.raw.get("vol_threshold", 0.0)),
        )
    as_of = acct.snapshot_date if acct else None
    soon = []
    if as_of is not None:
        horizon = as_of + timedelta(days=EARNINGS_AHEAD_DAYS)
        for r in held:
            nxt = events.next_earnings(r.symbol, as_of)
            if nxt is not None and nxt <= horizon:
                soon.append(r.symbol)
    flag = s.get(SystemFlag, "trading_enabled")
    report = s.scalars(
        select(JobRun.status)
        .where(JobRun.mode == mode, JobRun.job_name == "daily_report")
        .order_by(JobRun.trade_date.desc())
        .limit(1)
    ).first()
    return PortfolioOverview(
        as_of=as_of,
        net_liquidation=netliq,
        cash=float(acct.cash) if acct else None,
        summary=PortfolioSummaryOut(**vars(summ)),
        risk=RiskOut(**vars(risk)),
        top5=top5,
        max_single=top1,
        exposure=sum(weights.values()) if netliq else None,
        timing=timing,
        allocation=_allocation(held, netliq, acct, det, target) if netliq and acct else [],
        earnings_next_week=soon,
        reconcile=ReconcileOut(
            trading_enabled=flag is not None and flag.value == "true", last_report_status=report
        ),
    )


def _allocation(
    held: Sequence[PositionSnapshot],
    netliq: float,
    acct: AccountSnapshotRow,
    det: Detail,
    target: Mapping[str, float],
) -> list[SleeveAlloc]:
    actual = {"momentum": 0.0, "swing": 0.0, "other": 0.0}
    for r in held:
        w = float(r.market_value) / netliq
        share = det.sleeve_share(r.symbol)
        if not share:
            actual["other"] += w
        for sl, k in share.items():
            actual[sl] += w * k
    out = [
        SleeveAlloc(sleeve=sl, actual=actual[sl], target=det.sleeve_target(sl))
        for sl in ("momentum", "swing")
    ]
    cash_target = max(0.0, 1 - sum(target.values())) if target else None
    out.append(SleeveAlloc(sleeve="cash", actual=float(acct.cash) / netliq, target=cash_target))
    if actual["other"]:
        out.append(SleeveAlloc(sleeve="other", actual=actual["other"], target=0.0))
    return out


# ---- 净值对比 ----


def performance(s: Session, mode: Mode, stores: Stores) -> PerformanceOut:
    nav = nav_series(s, mode)
    if not len(nav):
        return PerformanceOut(points=[], rebalance_dates=[])
    dd = an.drawdown(nav)
    benches = {b: _bench_on(stores, b, nav.index) for b in BENCHMARKS}

    def norm(x: pd.Series, i: int) -> float | None:
        base, v = x.iloc[0], x.iloc[i]
        return None if pd.isna(base) or pd.isna(v) or not base else float(v / base)

    points = [
        PerfPoint(
            date=pd.Timestamp(t).date(),
            nav=float(nav.iloc[i]),
            portfolio=float(nav.iloc[i] / nav.iloc[0]),
            qqq=norm(benches["QQQ"], i),
            spy=norm(benches["SPY"], i),
            drawdown=float(dd.iloc[i]),
        )
        for i, t in enumerate(nav.index)
    ]
    sigs = list(reversed(signals_of(s, mode)))
    rebal = [
        x.signal_date
        for i, x in enumerate(sigs)
        if i == 0 or dict(x.target_weights) != dict(sigs[i - 1].target_weights)
    ]
    return PerformanceOut(points=points, rebalance_dates=rebal)


# ---- 持仓 ----


def _state(
    sleeve: str | None, close: pd.Series, det: Detail, symbol: str
) -> tuple[str | None, str]:
    if sleeve in ("swing", "both"):
        c, ma = an.last(close), an.last(an.sma(close, MA_EXIT)) if len(close) else None
        st = an.signal_state("swing", c, ma, None, det.top_n)
        note = f"距 10 日线 {c / ma - 1:+.1%}" if c is not None and ma else ""
        return st, note
    if sleeve == "momentum":
        r = det.rank(symbol)
        st = an.signal_state("momentum", None, None, r, det.top_n)
        return st, f"排名 {r}/{det.top_n}" if r else "排名 50 名之外"
    if sleeve == "exit":
        return "exit", "不在目标中，将卖出"
    return None, ""


def holdings(s: Session, mode: Mode, stores: Stores, events: EventCalendar) -> list[HoldingRow]:
    snaps = snapshots_of(s, mode)
    held = latest_positions(snaps)
    if not held:
        return []
    acct = last_account(s, mode)
    netliq = float(acct.net_liquidation) if acct else None
    sigs = signals_of(s, mode, 1)
    sig = sigs[0] if sigs else None
    det = detail_of(sig)
    target = dict(sig.target_weights) if sig else {}
    by_day = held_by_day(snaps)
    as_of = held[0].snapshot_date
    out = []
    for r in held:
        close = _close(stores, r.symbol)
        price = an.last(close)
        prev = an.last(close.iloc[:-1]) if len(close) >= 2 else None
        w = float(r.market_value) / netliq if netliq else None
        tw = float(target.get(r.symbol, 0.0))
        sleeve = det.sleeve_of(r.symbol, target)
        state, note = _state(sleeve, close, det, r.symbol)
        out.append(
            HoldingRow(
                symbol=r.symbol, sleeve=sleeve, qty=r.qty, avg_cost=float(r.avg_cost),
                price=price,
                change_pct=price / prev - 1 if price is not None and prev else None,
                market_value=float(r.market_value), weight=w, target_weight=tw,
                deviation=w - tw if w is not None else None,
                unrealized_pnl=(price - float(r.avg_cost)) * r.qty if price is not None else None,
                state=state, state_note=note,
                next_earnings=events.next_earnings(r.symbol, as_of),
                holding_days=an.holding_days(by_day, r.symbol),
            )
        )  # fmt: skip
    return out


# ---- 收益贡献、日历 ----


def contributions(s: Session, mode: Mode, stores: Stores, period: str) -> list[ContributionRow]:
    snaps = snapshots_of(s, mode)
    if not snaps:
        return []
    qty = (
        pd.DataFrame(
            [(pd.Timestamp(r.snapshot_date), r.symbol, r.qty) for r in snaps],
            columns=["date", "symbol", "qty"],
        )
        .pivot_table(index="date", columns="symbol", values="qty", aggfunc="sum")
        .fillna(0)
    )
    close = pd.DataFrame({sym: _close(stores, str(sym)) for sym in qty.columns})
    if close.empty:
        return []
    last = snaps[-1].snapshot_date
    start = last if period == "day" else last.replace(day=1)
    pnl = an.contributions(qty, close, start, last)
    sigs = signals_of(s, mode, 1)
    sig = sigs[0] if sigs else None
    det, target = detail_of(sig), dict(sig.target_weights) if sig else {}
    out = []
    for sym, v in sorted(pnl.items(), key=lambda kv: -abs(kv[1])):
        share = det.sleeve_share(sym)
        if len(share) == 2:
            out += [ContributionRow(symbol=sym, sleeve=sl, pnl=v * k) for sl, k in share.items()]
        else:
            out.append(ContributionRow(symbol=sym, sleeve=det.sleeve_of(sym, target), pnl=v))
    return out


def calendar(s: Session, mode: Mode, month: str | None) -> list[CalendarDayOut]:
    nav = nav_series(s, mode)
    if not len(nav):
        return []
    if month is None:
        t = pd.Timestamp(nav.index[-1])
        year, mon = t.year, t.month
    else:
        year, mon = int(month[:4]), int(month[5:7])
    return [CalendarDayOut(**vars(d)) for d in an.pnl_calendar(nav, year, mon)]


# ---- 下次调仓预览 ----


def _min_diff(strategy: str) -> float:
    if strategy in STRATEGIES:
        v = get_strategy(strategy).min_weight_diff
        if v is not None:
            return float(v)
    return DEFAULT_MIN_DIFF


def _reason(sym: str, side: str, held: bool, det: Detail, prev: Detail) -> str:
    sleeves = (det.raw or {}).get("sleeves", {})
    prev_sleeves = (prev.raw or {}).get("sleeves", {})
    if not held:
        if sym in sleeves.get("momentum", {}):
            r = det.rank(sym)
            return f"动量排名第 {r}" if r else "动量选入"
        return "波段进场信号" if sym in sleeves.get("swing", {}) else "新进目标"
    in_target = sym in sleeves.get("momentum", {}) or sym in sleeves.get("swing", {})
    if det.raw is not None and not in_target:
        if sym in prev_sleeves.get("momentum", {}):
            return f"排名跌出前 {det.top_n}"
        if sym in prev_sleeves.get("swing", {}):
            return "波段离场"
        return "不在目标中"
    f, pf = det.factor, prev.factor
    if f is not None and pf is not None:
        if f < pf and side == "SELL":
            return "择时减仓"
        if f > pf and side == "BUY":
            return "择时恢复"
    return "权重调整"


def rebalance_preview(
    s: Session, mode: Mode, stores: Stores, costs: CostSettings
) -> RebalancePreview:
    sigs = signals_of(s, mode, 2)
    acct = last_account(s, mode)
    if not sigs or acct is None:
        return RebalancePreview(signal_date=None, rows=[], turnover=0.0, est_commission=None)
    sig = sigs[0]
    det, prev = detail_of(sig), detail_of(sigs[1] if len(sigs) > 1 else None)
    netliq = float(acct.net_liquidation)
    current = {
        r.symbol: float(r.market_value) / netliq for r in latest_positions(snapshots_of(s, mode))
    }
    target = {k: float(v) for k, v in sig.target_weights.items()}
    min_diff = _min_diff(sig.strategy)
    rows: list[PreviewRow] = []
    turnover, fee, has_fee = 0.0, 0.0, False
    for sym in sorted(set(current) | set(target)):
        w0, w1 = current.get(sym, 0.0), target.get(sym, 0.0)
        diff = w1 - w0
        turnover += abs(diff) / 2
        if abs(diff) < min_diff:
            continue
        side = "BUY" if diff > 0 else "SELL"
        rows.append(PreviewRow(
            side=side, symbol=sym, sleeve=det.sleeve_of(sym, target),
            weight_from=w0, weight_to=w1, reason=_reason(sym, side, sym in current, det, prev),
        ))  # fmt: skip
        price = an.last(_close(stores, sym))
        if price:
            est = an.estimate_commission(int(abs(diff) * netliq / price), price, costs)
            if est is not None:
                fee, has_fee = fee + est, True
    rows.sort(key=lambda r: (r.side != "SELL", -abs(r.weight_to - r.weight_from)))
    return RebalancePreview(
        signal_date=sig.signal_date, rows=rows, turnover=turnover,
        est_commission=fee if has_fee else None,
    )  # fmt: skip


# ---- 已平仓 ----


def closed_trades(s: Session, mode: Mode) -> ClosedTradesOut:
    fills = list(s.scalars(select(FillRow).where(FillRow.mode == mode)).all())
    trades = an.closed_trades(fills)
    stats = an.trade_stats(trades)
    refs = {
        o.broker_order_id: float(o.reference_price)
        for o in s.scalars(
            select(Order).where(Order.mode == mode, Order.reference_price.is_not(None))
        ).all()
        if o.broker_order_id and o.reference_price
    }
    slips = [
        (float(f.price) - refs[f.broker_order_id]) / refs[f.broker_order_id]
        * (1 if f.side.upper() == "BUY" else -1)
        for f in fills
        if f.broker_order_id in refs
    ]  # fmt: skip
    return ClosedTradesOut(
        trades=[
            ClosedTradeOut(**vars(t))
            for t in sorted(trades, key=lambda t: t.close_date, reverse=True)
        ],
        stats=TradeStatsOut(
            count=stats.count, win_rate=stats.win_rate, avg_pnl=stats.avg_pnl,
            avg_slippage=sum(slips) / len(slips) if slips else None,
        ),
    )  # fmt: skip
