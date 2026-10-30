"""月度动量选股（设计：specs/2026-10-30-momentum-combo-design.md 第 4.2 节）。

每月最后一个交易日收盘后，在“当时”的指数成分股里按流动性、趋势、放量过滤，再按约 6 个月涨幅
取前 top_n 只等权持有到下个月；下一交易日调仓。可选：财报前离场、当月不补回。

- 成分股由 Context.universe 提供；价格为 Context.field 的含分红复权口径。
- 纯计算：排名按 (调仓日, 参数) 在实例内缓存，只是记忆化，不改变结果。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, fields
from datetime import date, timedelta
from typing import Any, ClassVar

import pandas as pd

from quant.core.calendar import TradingCalendar, get_calendar
from quant.strategies.base import Context, Strategy


@dataclass(frozen=True)
class MomentumParams:
    top_n: int = 4
    liquidity_top: int = 200
    min_price: float = 5.0
    min_history: int = 252
    trend_ma: int = 120
    lookback: int = 126
    dv_window: int = 60
    dv_short: int = 20
    dv_long: int = 120
    earnings_exit: bool = False
    earnings_exit_days: int = 2

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> MomentumParams:
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(params) - known)
        if unknown:
            raise ValueError(f"未知参数：{unknown}")
        return cls(**params)

    @property
    def history_needed(self) -> int:
        """排序需要的最少交易日数。"""
        return max(self.min_history, self.lookback + 1, self.trend_ma, self.dv_long)


def _tail_mean(df: pd.DataFrame, n: int) -> pd.Series:
    """最近 n 行的均值；不足 n 个有效值为 NaN。"""
    tail = df.tail(n)
    return tail.mean().where(tail.count() == n)


def rank_momentum(
    close: pd.DataFrame, volume: pd.DataFrame, members: Collection[str], p: MomentumParams
) -> list[str]:
    """close / volume 为截至排序日（最后一行）的宽表。返回全部合格代码：涨幅降序，同分按代码。"""
    cols = sorted(s for s in members if s in close.columns and s in volume.columns)
    if not cols or close.empty:
        return []
    c, v = close[cols], volume[cols]
    last = c.iloc[-1]
    ok = (c.count() >= p.min_history) & (last > p.min_price)
    dv = c * v
    liq = _tail_mean(dv, p.dv_window)[ok].dropna().nlargest(p.liquidity_top).index
    trend = _tail_mean(c, p.trend_ma)
    expanding = _tail_mean(dv, p.dv_short) > _tail_mean(dv, p.dv_long)
    ret = last / c.iloc[-1 - p.lookback] - 1 if len(c) > p.lookback else pd.Series(dtype=float)
    picked = [
        (float(ret[s]), s)
        for s in liq
        if last[s] > trend[s] and bool(expanding[s]) and s in ret and pd.notna(ret[s])
    ]
    picked.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, s in picked]


def rebalance_day(cal: TradingCalendar, d: date) -> date:
    """d 是月末交易日时为 d，否则为上个月的最后一个交易日。"""
    if cal.is_month_end(d):
        return d
    prev = d.replace(day=1) - timedelta(days=1)
    while not cal.is_month_end(prev):
        prev = cal.previous_trading_day(prev)
    return prev


class MomentumSelect(Strategy):
    name = "momentum_select"
    min_weight_diff: ClassVar[float | None] = 0.10
    # 个股财报跳空可达 40–50%（如 AMD 2016-04 +52%）
    max_daily_move: ClassVar[float] = 0.6
    uses_universe: ClassVar[bool] = True

    def __init__(self, calendar: TradingCalendar | None = None) -> None:
        self._cal = calendar or get_calendar()
        self._cache: dict[tuple[date, MomentumParams], list[str]] = {}

    def symbols(self, params: Mapping[str, Any]) -> list[str]:
        # 候选池随时间变化：由调用方按 Constituents.all_symbols 准备数据
        return []

    def warmup_days(self, params: Mapping[str, Any]) -> int:
        return MomentumParams.from_mapping(params).min_history + 30

    def _ranking(self, ctx: Context, me: date, p: MomentumParams) -> list[str]:
        key = (me, p)
        if key not in self._cache:
            if ctx.universe is None:
                raise ValueError("momentum_select 需要成分股快照（Context.universe）")
            members = sorted(ctx.universe.members(me))
            start = me - timedelta(days=int((p.history_needed + 10) * 1.6))
            close = ctx.field(members, "close", start, me)
            volume = ctx.field(members, "volume", start, me)
            self._cache[key] = rank_momentum(close, volume, members, p)
        return self._cache[key]

    def target_weights(self, ctx: Context) -> dict[str, float]:
        p = MomentumParams.from_mapping(ctx.params)
        if ctx.universe is None:
            raise ValueError("momentum_select 需要成分股快照（Context.universe）")
        me = rebalance_day(self._cal, ctx.as_of)
        picks = self._ranking(ctx, me, p)[: p.top_n]
        if p.earnings_exit:
            horizon = ctx.as_of
            for _ in range(p.earnings_exit_days):
                horizon = self._cal.next_trading_day(horizon)
            kept = []
            for s in picks:
                r = ctx.events.next_earnings(s, me)
                if r is None or r > horizon:
                    kept.append(s)
            picks = kept
        return {s: 1.0 / p.top_n for s in picks}
