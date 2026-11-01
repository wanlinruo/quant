"""面板组合与个股指标的纯计算：无 I/O，输入 pandas 对象，样本不足时返回 None（不返回 NaN/inf）。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

import pandas as pd

from quant.core.settings import CostSettings
from quant.strategies.swing_rules import rsi as _rsi
from quant.strategies.swing_rules import sma as _sma

TRADING_DAYS = 252
MIN_RETURNS = 20  # 波动率、夏普、Beta、VaR 至少需要的日收益率个数
NEAR_EXIT_PCT = 0.03  # 波段仓：收盘价高于 10 日线不足 3% 视为接近退出
NEAR_EXIT_RANKS = 2  # 动量仓：排名在最后 2 名视为接近退出

SignalState = Literal["ok", "near", "exit"]


def _num(x: float | None) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def last(s: pd.Series) -> float | None:
    """序列最后一个有效值；没有时为 None。"""
    s = s.dropna()
    return _num(s.iloc[-1]) if len(s) else None


def daily_returns(values: pd.Series) -> pd.Series:
    return values.pct_change(fill_method=None).dropna()


def drawdown(values: pd.Series) -> pd.Series:
    return values / values.cummax() - 1


def _ret_between(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """s 在 [start, end] 区间内首尾两点的收益；不足两点时为 None。"""
    seg = s[(s.index >= start) & (s.index <= end)].dropna()
    if len(seg) < 2 or seg.iloc[0] == 0:
        return None
    return _num(seg.iloc[-1] / seg.iloc[0] - 1)


@dataclass(frozen=True)
class PortfolioSummary:
    today_pnl: float | None
    today_return: float | None
    total_return: float | None
    qqq_return: float | None
    spy_return: float | None
    max_drawdown: float | None
    current_drawdown: float | None
    ann_vol: float | None
    sharpe: float | None


def summary(nav: pd.Series, qqq: pd.Series, spy: pd.Series) -> PortfolioSummary:
    nav = nav.dropna()
    if len(nav) < 2:
        return PortfolioSummary(None, None, None, None, None, None, None, None, None)
    rets = daily_returns(nav)
    dd = drawdown(nav)
    start, end = nav.index[0], nav.index[-1]
    vol = sharpe = None
    if len(rets) >= MIN_RETURNS:
        sd = float(rets.std(ddof=1))
        vol = _num(sd * math.sqrt(TRADING_DAYS))
        sharpe = _num(float(rets.mean()) / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None
    return PortfolioSummary(
        today_pnl=_num(nav.iloc[-1] - nav.iloc[-2]),
        today_return=_num(rets.iloc[-1]),
        total_return=_ret_between(nav, start, end),
        qqq_return=_ret_between(qqq, start, end),
        spy_return=_ret_between(spy, start, end),
        max_drawdown=_num(dd.min()),
        current_drawdown=_num(dd.iloc[-1]),
        ann_vol=vol,
        sharpe=sharpe,
    )


@dataclass(frozen=True)
class RiskStats:
    beta_spy: float | None
    corr_qqq: float | None
    var95_1d: float | None  # 金额；正数表示 95% 置信下一日可能的亏损


def risk(nav: pd.Series, spy: pd.Series, qqq: pd.Series, window: int = 60) -> RiskStats:
    df = pd.concat(
        {"p": daily_returns(nav.dropna()), "spy": daily_returns(spy.dropna()),
         "qqq": daily_returns(qqq.dropna())},
        axis=1,
    ).dropna().iloc[-window:]  # fmt: skip
    if len(df) < MIN_RETURNS:
        return RiskStats(None, None, None)
    var_spy = float(df["spy"].var(ddof=1))
    beta = _num(float(df["p"].cov(df["spy"])) / var_spy) if var_spy > 0 else None
    corr = _num(float(df["p"].corr(df["qqq"])))
    var95 = _num(max(0.0, -float(df["p"].quantile(0.05))) * float(nav.dropna().iloc[-1]))
    return RiskStats(beta, corr, var95)


def concentration(weights: Mapping[str, float]) -> tuple[float, float]:
    """前 5 大持仓权重合计、最大单一持仓权重。"""
    ws = sorted(weights.values(), reverse=True)
    return float(sum(ws[:5])), float(ws[0]) if ws else 0.0


@dataclass(frozen=True)
class CalendarDay:
    date: date
    pnl: float | None
    ret: float | None


def pnl_calendar(nav: pd.Series, year: int, month: int) -> list[CalendarDay]:
    """该月每个有快照的交易日：相对前一快照日的净值变化（前一日可以在上个月）。"""
    nav = nav.dropna()
    idx = pd.DatetimeIndex(nav.index)
    values, prevs = nav.to_numpy(dtype=float), nav.shift(1).to_numpy(dtype=float)
    out = []
    for t, v, p in zip(idx, values, prevs, strict=True):
        if t.year != year or t.month != month:
            continue
        has_prev = math.isfinite(p)
        pnl = _num(v - p) if has_prev else None
        ret = _num(v / p - 1) if has_prev and p != 0 else None
        out.append(CalendarDay(t.date(), pnl, ret))
    return out


def contributions(
    qty: pd.DataFrame, close: pd.DataFrame, start: date, end: date
) -> dict[str, float]:
    """每只股票在 [start, end] 各日的收益贡献之和：前一快照日持仓数量 × 当日收盘价变化。

    qty、close 的索引为快照日，qty 为当日收盘后的持仓数量。
    """
    qty = qty.sort_index()
    close = close.reindex(index=qty.index, columns=qty.columns)
    daily = qty.shift(1) * close.diff()
    mask = (daily.index >= pd.Timestamp(start)) & (daily.index <= pd.Timestamp(end))
    total = daily[mask].sum(min_count=1)
    return {str(s): float(v) for s, v in total.items() if pd.notna(v) and v != 0}


class FillLike(Protocol):
    symbol: str
    side: str
    qty: int
    price: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    open_date: date
    close_date: date
    qty: int
    open_price: float
    close_price: float
    pnl: float
    ret: float
    days: int


@dataclass
class _Lot:
    opened: date
    price: float
    qty: int


def closed_trades(fills: Sequence[FillLike]) -> list[ClosedTrade]:
    """按代码先进先出配对买卖成交；一笔卖出可拆成多段。未平仓部分不计。"""
    lots: dict[str, list[_Lot]] = {}
    out: list[ClosedTrade] = []
    for f in sorted(fills, key=lambda x: x.filled_at):
        price, day = float(f.price), f.filled_at.date()
        if f.side.upper() == "BUY":
            lots.setdefault(f.symbol, []).append(_Lot(day, price, f.qty))
            continue
        remaining = f.qty
        queue = lots.get(f.symbol, [])
        while remaining > 0 and queue:
            lot = queue[0]
            n = min(remaining, lot.qty)
            out.append(ClosedTrade(
                symbol=f.symbol, open_date=lot.opened, close_date=day, qty=n,
                open_price=lot.price, close_price=price, pnl=(price - lot.price) * n,
                ret=price / lot.price - 1 if lot.price else 0.0, days=(day - lot.opened).days,
            ))  # fmt: skip
            remaining -= n
            lot.qty -= n
            if lot.qty == 0:
                queue.pop(0)
    return out


@dataclass(frozen=True)
class TradeStats:
    count: int
    win_rate: float | None
    avg_pnl: float | None


def trade_stats(trades: Sequence[ClosedTrade]) -> TradeStats:
    if not trades:
        return TradeStats(0, None, None)
    wins = sum(1 for t in trades if t.pnl > 0)
    return TradeStats(len(trades), wins / len(trades), sum(t.pnl for t in trades) / len(trades))


def holding_periods(snapshots: Mapping[date, set[str]], symbol: str) -> list[tuple[date, date]]:
    """symbol 在连续快照日中出现的区间（中间某个快照日没有持有即断开）。"""
    out: list[tuple[date, date]] = []
    start: date | None = None
    prev: date | None = None
    for d in sorted(snapshots):
        if symbol in snapshots[d]:
            start = d if start is None else start
            prev = d
        elif start is not None and prev is not None:
            out.append((start, prev))
            start = None
    if start is not None and prev is not None:
        out.append((start, prev))
    return out


def holding_days(snapshots: Mapping[date, set[str]], symbol: str) -> int:
    """截至最新快照连续持有的快照日数；最新快照未持有为 0。"""
    n = 0
    for d in sorted(snapshots, reverse=True):
        if symbol not in snapshots[d]:
            break
        n += 1
    return n


def signal_state(
    sleeve: str, close: float | None, ma10: float | None, rank: int | None, top_n: int
) -> SignalState | None:
    """持仓的信号状态（计划“我做的决定”第 4 条）。"""
    if sleeve == "swing":
        if close is None or ma10 is None or ma10 <= 0:
            return None
        ratio = close / ma10
        if ratio < 1:
            return "exit"
        return "near" if ratio <= 1 + NEAR_EXIT_PCT else "ok"
    if sleeve == "momentum":
        if rank is None or rank > top_n:
            return "exit"
        return "near" if rank > top_n - NEAR_EXIT_RANKS else "ok"
    return None


def estimate_commission(qty: int, price: float, costs: CostSettings) -> float | None:
    """一笔订单的佣金 + 平台费估算（与 SimBroker 的成本模型同一公式）；佣金未配置时为 None。"""
    if costs.commission_per_share is None or costs.min_commission is None:
        return None
    q = Decimal(qty)
    parts = [max(costs.commission_per_share * q, costs.min_commission)]
    if costs.platform_per_share or costs.platform_min:
        parts.append(max(costs.platform_per_share * q, costs.platform_min))
    if costs.max_fee_pct is not None:
        cap = Decimal(str(price)) * q * costs.max_fee_pct
        parts = [min(p, cap) for p in parts]
    return round(float(sum(parts, Decimal(0))), 2)


def sma(close: pd.Series, n: int) -> pd.Series:
    return _sma(close, n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    return _rsi(close, n)


def atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> float | None:
    """最近 n 日平均真实波幅 / 最新收盘价。"""
    if len(close.dropna()) < n + 1:
        return None
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    c = close.iloc[-1]
    return _num(float(tr.iloc[-n:].mean()) / float(c)) if c else None


PERIODS: dict[str, pd.DateOffset] = {
    "1W": pd.DateOffset(weeks=1),
    "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
}


def period_returns(close: pd.Series, as_of: date) -> dict[str, float | None]:
    """区间收益：1D 为最近两根；其余以 as_of 往前推的日期当天或之前最近的收盘为基准。"""
    s = close.dropna()
    s = s[s.index <= pd.Timestamp(as_of)]
    out: dict[str, float | None] = {"1D": None}
    if len(s) >= 2 and s.iloc[-2]:
        out["1D"] = _num(s.iloc[-1] / s.iloc[-2] - 1)
    end = s.iloc[-1] if len(s) else None

    def from_base(cut: pd.Timestamp) -> float | None:
        base = s[s.index <= cut]
        if end is None or not len(base) or base.index[-1] == s.index[-1] or not base.iloc[-1]:
            return None
        return _num(end / base.iloc[-1] - 1)

    t = pd.Timestamp(as_of)
    for key, off in PERIODS.items():
        out[key] = from_base(t - off)
    out["YTD"] = from_base(pd.Timestamp(year=t.year - 1, month=12, day=31))
    return {k: out[k] for k in ("1D", "1W", "1M", "3M", "6M", "YTD", "1Y")}


@dataclass(frozen=True)
class PreviewOrder:
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    weight_from: float
    weight_to: float  # 目标权重（未扣现金缓冲）


def preview_orders(
    positions: Mapping[str, int],
    target_weights: Mapping[str, float],
    prices: Mapping[str, float],
    *,
    net_liquidation: float,
    cash_buffer: float,
    min_weight_diff: float,
    min_trade_usd: float,
) -> list[PreviewOrder]:
    """按执行器的规则（quant.engine.orders.plan_orders，修改时两边同步）列出会下的单：

    目标股数 = floor(净值 × 目标权重 × (1 − 现金缓冲) / 价格)；清仓总是执行；
    新开仓只受最小金额限制；其余按有效目标权重与当前权重之差 < min_weight_diff 跳过。
    不含可用资金约束（预览只读，不模拟买单被资金截断）。缺价格的代码跳过。
    """
    out: list[PreviewOrder] = []
    if net_liquidation <= 0:
        return out
    investable = net_liquidation * (1 - cash_buffer)
    for sym in sorted(set(positions) | set(target_weights)):
        price = prices.get(sym)
        if not price or price <= 0:
            continue
        w = float(target_weights.get(sym, 0.0))
        current = int(positions.get(sym, 0))
        target = math.floor(investable * w / price) if w > 0 else 0
        diff = target - current
        if diff == 0:
            continue
        current_w = current * price / net_liquidation
        if target != 0:
            too_small = abs(diff) * price < min_trade_usd
            drift = abs(w * (1 - cash_buffer) - current_w) < min_weight_diff
            if too_small or (drift and current != 0):
                continue
        out.append(PreviewOrder(sym, "BUY" if diff > 0 else "SELL", abs(diff), current_w, w))
    return out
