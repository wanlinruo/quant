"""七姐妹均线回踩波段策略（设计：docs/superpowers/specs/2026-10-29-swing-pullback-design.md）。

- 个股与 QQQ：上升趋势中回踩支撑均线并止跌时买入；纳指空头用 1 倍反向 ETF PSQ（只买入）。
- 持仓状态（进场日、进场价、持有天数、是否已有浮盈）不从外部读取：每次运行对 as_of 之前
  最近 replay_days 个交易日确定性重放得到，回测与实盘行为一致，策略仍无 I/O。
- 信号日 t 收盘后判断，t+1 开盘成交（实盘为 09:50 执行）；重放中进场价取 t+1 开盘价。
- 已持仓位的目标权重固定为进场时的权重；min_weight_diff = 0.10 使价格漂移不触发来回调仓。

compute_inputs 计算指标与信号（向量化），simulate 逐日重放状态（便于单独测试）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date, timedelta
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from quant.core.calendar import TradingCalendar, get_calendar
from quant.strategies.base import Context, Strategy
from quant.strategies.events import EventCalendar
from quant.strategies.swing_rules import ConfirmMode, long_setup, short_setup

FRAME_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class SwingParams:
    stocks: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "MU")
    index: str = "QQQ"
    inverse: str = "PSQ"
    trend_ma: int = 120
    slope_days: int = 0
    support_ma: int = 20
    tol: float = 0.01
    touch_days: int = 3
    confirm: ConfirmMode = "close_strength"
    exit_ma: int = 5
    arm_pct: float = 0.02
    stop_pct: float = 0.07
    support_break: float = 0.03
    max_hold: int = 30
    max_positions: int = 4
    base_weight: float = 0.25
    max_weight: float = 0.30
    vol_window: int = 20
    earnings_exit_days: int = 2
    earnings_no_entry_days: int = 5
    fomc_exit: bool = False
    replay_days: int = 250
    use_inverse: bool = True  # False：不用反向 ETF 表达纳指空头（组合策略的 V2 仓）

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> SwingParams:
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(params) - known)
        if unknown:
            raise ValueError(f"未知参数：{unknown}")
        kw = dict(params)
        if "stocks" in kw:
            kw["stocks"] = tuple(kw["stocks"])
        return cls(**kw)

    @property
    def tradables(self) -> list[str]:
        """可以做多的标的：个股 + QQQ。"""
        return [*self.stocks, self.index]

    @property
    def symbols(self) -> list[str]:
        return [*self.tradables, self.inverse] if self.use_inverse else self.tradables


@dataclass(frozen=True)
class Inputs:
    """宽表（日期 × 代码）。PSQ 列：score 为 QQQ 的空头强度，其余为 PSQ 自身价格。"""

    open: pd.DataFrame
    close: pd.DataFrame
    sma_support: pd.DataFrame
    sma_exit: pd.DataFrame
    score: pd.DataFrame
    vol: pd.DataFrame
    long_sig: pd.DataFrame
    short_sig: pd.Series  # QQQ 反抽受压 → 买 PSQ


@dataclass
class _Holding:
    entry_day: date
    entry_price: float
    weight: float
    days_held: int = 0
    armed: bool = False


def compute_inputs(frames: Mapping[str, pd.DataFrame], p: SwingParams) -> Inputs:
    """frames：字段 → 宽表（含分红复权的开高低收与成交量）。"""
    o, h, lo, c, v = (frames[f].reindex(columns=p.symbols) for f in FRAME_FIELDS)
    long_sig = pd.DataFrame(False, index=c.index, columns=p.symbols)
    for s in p.tradables:
        long_sig[s] = long_setup(
            o[s], h[s], lo[s], c[s], v[s],
            trend_ma=p.trend_ma, slope_days=p.slope_days, support_ma=p.support_ma,
            tol=p.tol, touch_days=p.touch_days, confirm=p.confirm,
        )  # fmt: skip
    q = p.index
    short_sig = short_setup(
        o[q], h[q], lo[q], c[q],
        trend_ma=p.trend_ma, support_ma=p.support_ma, tol=p.tol, touch_days=p.touch_days,
    )  # fmt: skip
    trend = c.rolling(p.trend_ma, min_periods=p.trend_ma).mean()
    score = c / trend - 1
    if p.use_inverse:
        score[p.inverse] = 1 - c[q] / trend[q]
    return Inputs(
        open=o,
        close=c,
        sma_support=c.rolling(p.support_ma, min_periods=p.support_ma).mean(),
        sma_exit=c.rolling(p.exit_ma, min_periods=p.exit_ma).mean(),
        score=score,
        vol=c.pct_change(fill_method=None).rolling(p.vol_window, min_periods=p.vol_window).std(),
        long_sig=long_sig,
        short_sig=short_sig,
    )


class _View:
    """把重放窗口内的数据转成 numpy 数组，并预先取出窗口与其后几天的交易日（性能）。"""

    def __init__(self, inp: Inputs, p: SwingParams, cal: TradingCalendar, as_of: date) -> None:
        idx = inp.close.index
        win = idx[idx <= pd.Timestamp(as_of)][-p.replay_days :]
        rows = idx.get_indexer(win)
        cols = list(inp.close.columns)
        self.col = {s: i for i, s in enumerate(cols)}
        self.days: list[date] = [ts.date() for ts in win]

        def arr(df: pd.DataFrame) -> np.ndarray:
            return df.reindex(columns=cols).to_numpy(dtype=float)[rows]

        self.open, self.close = arr(inp.open), arr(inp.close)
        self.sma_support, self.sma_exit = arr(inp.sma_support), arr(inp.sma_exit)
        self.score, self.vol = arr(inp.score), arr(inp.vol)
        self.long_sig = inp.long_sig.reindex(columns=cols).fillna(False).to_numpy(dtype=bool)[rows]
        self.short_sig = inp.short_sig.reindex(win).fillna(False).to_numpy(dtype=bool)
        self._cal = cal
        horizon = max(p.earnings_exit_days, p.earnings_no_entry_days, 1) + 1
        end = as_of
        for _ in range(horizon):
            end = cal.next_trading_day(end)
        first = self.days[0] if self.days else as_of
        self._td = cal.trading_days(first, end)
        self._pos = {d: i for i, d in enumerate(self._td)}

    def ahead(self, t: date, n: int) -> date:
        """t 之后第 n 个交易日。"""
        i = self._pos.get(t)
        if i is not None and i + n < len(self._td):
            return self._td[i + n]
        d = t
        for _ in range(n):
            d = self._cal.next_trading_day(d)
        return d

    def num(self, a: np.ndarray, i: int, sym: str) -> float:
        j = self.col.get(sym)
        return float(a[i, j]) if j is not None else float("nan")


def simulate(
    inp: Inputs, p: SwingParams, events: EventCalendar, cal: TradingCalendar, as_of: date
) -> dict[str, float]:
    """重放到 as_of 收盘，返回目标权重（含当日新进场、去掉当日离场）。"""
    v = _View(inp, p, cal, as_of)
    holdings: dict[str, _Holding] = {}
    pending: dict[str, float] = {}  # 信号日决定、次日开盘进场
    for i, t in enumerate(v.days):
        for s, w in pending.items():
            px = v.num(v.open, i, s)
            if px == px:  # 开盘价缺失则放弃进场
                holdings[s] = _Holding(t, px, w)
        pending = {}

        for s, hold in list(holdings.items()):
            c = v.num(v.close, i, s)
            if c != c:
                continue  # 当日缺行情：维持
            hold.days_held += 1
            hold.armed |= c >= hold.entry_price * (1 + p.arm_pct)
            if _should_exit(v, p, events, i, t, s, hold, c):
                del holdings[s]

        if events.is_fomc(v.ahead(t, 1)):
            continue
        slots = p.max_positions - len(holdings)
        if slots <= 0:
            continue
        picks = _candidates(v, p, events, i, t, holdings)[:slots]
        if picks:
            pending = _size(v, p, i, picks, holdings)
    out = {s: h.weight for s, h in holdings.items()}
    out.update(pending)
    return out


def _should_exit(
    v: _View,
    p: SwingParams,
    events: EventCalendar,
    i: int,
    t: date,
    s: str,
    hold: _Holding,
    c: float,
) -> bool:
    if hold.days_held >= p.max_hold or c <= hold.entry_price * (1 - p.stop_pct):
        return True
    if p.fomc_exit and events.is_fomc(v.ahead(t, 1)):
        return True
    if s == p.inverse:
        return v.num(v.close, i, p.index) > v.num(v.sma_exit, i, p.index)
    if c < v.num(v.sma_support, i, s) * (1 - p.support_break):
        return True
    if hold.armed and c < v.num(v.sma_exit, i, s):
        return True
    if s in p.stocks:
        r = events.next_earnings(s, t)
        if r is not None and r <= v.ahead(t, p.earnings_exit_days):
            return True
    return False


def _candidates(
    v: _View,
    p: SwingParams,
    events: EventCalendar,
    i: int,
    t: date,
    holdings: Mapping[str, _Holding],
) -> list[str]:
    horizon = v.ahead(t, p.earnings_no_entry_days)
    cands: list[tuple[float, str]] = []
    for s in p.tradables:
        j = v.col.get(s)
        if s in holdings or j is None or not v.long_sig[i, j]:
            continue
        if s in p.stocks:
            r = events.next_earnings(s, t)
            if r is not None and r <= horizon:
                continue
        cands.append((v.num(v.score, i, s), s))
    if p.use_inverse and p.inverse not in holdings and v.short_sig[i]:
        cands.append((v.num(v.score, i, p.inverse), p.inverse))
    cands = [(sc, s) for sc, s in cands if sc == sc]
    cands.sort(key=lambda x: (-x[0], x[1]))
    # QQQ 与 PSQ 方向相反，不同时持有：已持有一方时排除另一方；同日都出现时保留排名靠前的
    blocked = {p.index, p.inverse} & set(holdings)
    out: list[str] = []
    for _, s in cands:
        if s in (p.index, p.inverse):
            if blocked:
                continue
            blocked = {p.index, p.inverse}
        out.append(s)
    return out


def _size(
    v: _View,
    p: SwingParams,
    i: int,
    picks: Sequence[str],
    holdings: Mapping[str, _Holding],
) -> dict[str, float]:
    vols = {s: v.num(v.vol, i, s) for s in [*holdings, *picks]}
    valid = [x for x in vols.values() if x == x and x > 0]
    vbar = sum(valid) / len(valid) if valid else 0.0
    raw = {}
    for s in picks:
        x = vols[s]
        w = p.base_weight * vbar / x if x == x and x > 0 and vbar > 0 else p.base_weight
        raw[s] = min(p.max_weight, w)
    room = 1 - sum(h.weight for h in holdings.values())
    total = sum(raw.values())
    if total > room:
        scale = max(room, 0.0) / total
        raw = {s: w * scale for s, w in raw.items()}
    return {s: w for s, w in raw.items() if w > 0}


class SwingPullback(Strategy):
    name = "swing_pullback"
    min_weight_diff: ClassVar[float | None] = 0.10
    # 个股财报日常有 15–30% 的单日波动；40% 仍能识别 2:1 及以上未复权的拆股（−50%）
    max_daily_move: ClassVar[float] = 0.40

    def __init__(self, calendar: TradingCalendar | None = None) -> None:
        self._cal = calendar or get_calendar()

    def symbols(self, params: Mapping[str, Any]) -> list[str]:
        return SwingParams.from_mapping(params).symbols

    def earnings_symbols(self, params: Mapping[str, Any]) -> list[str]:
        return list(SwingParams.from_mapping(params).stocks)

    def warmup_days(self, params: Mapping[str, Any]) -> int:
        p = SwingParams.from_mapping(params)
        return p.trend_ma + p.replay_days + 30

    def target_weights(self, ctx: Context) -> dict[str, float]:
        p = SwingParams.from_mapping(ctx.params)
        need = p.trend_ma + p.replay_days + p.vol_window + 10
        start = ctx.as_of - timedelta(days=int(need * 1.5))
        frames = {f: ctx.field(p.symbols, f, start) for f in FRAME_FIELDS}
        return simulate(compute_inputs(frames, p), p, ctx.events, self._cal, ctx.as_of)
