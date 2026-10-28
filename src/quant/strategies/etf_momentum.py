"""ETF 动量轮动（PROJECT_PLAN 第 14 章），基线策略。纯计算、无 I/O。

- 每月最后一个交易日，计算标的池中各标的过去 lookback 个交易日的收益率（含分红复权价）。
- 持有收益率最高的 top_k 只，等权。
- 绝对动量过滤：选中标的收盘价低于其 sma_window 日均线，或收益率低于避险资产，
  则该份额改为持有避险资产（top_k=1 时即全部持有 SHY）。
- 非调仓日返回最近一个调仓日的权重。实现上无状态：任何一天都按“最近一个月末交易日”
  重新计算，因此回测与实盘、进程重启前后结果一致。
- 数据不足（如上市不足 lookback 天）的标的不参与排名；均线数据不足视为未通过过滤；
  避险资产数据不足时返回空仓。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from quant.core.calendar import TradingCalendar, get_calendar
from quant.strategies.base import Context, Strategy


@dataclass(frozen=True)
class MomentumParams:
    universe: tuple[str, ...] = ("SPY", "QQQ", "TLT", "GLD")
    safe_asset: str = "SHY"
    lookback: int = 126
    top_k: int = 1
    sma_window: int = 200

    def __post_init__(self) -> None:
        if not self.universe:
            raise ValueError("universe 不能为空")
        if self.safe_asset in self.universe:
            raise ValueError("safe_asset 不能在 universe 中")
        if self.lookback < 1 or self.sma_window < 1:
            raise ValueError("lookback、sma_window 必须为正")
        if not 1 <= self.top_k <= len(self.universe):
            raise ValueError(f"top_k 必须在 1 ~ {len(self.universe)} 之间")

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> MomentumParams:
        known = {"universe", "safe_asset", "lookback", "top_k", "sma_window"}
        unknown = set(params) - known
        if unknown:
            raise ValueError(f"未知参数：{sorted(unknown)}")
        kw = dict(params)
        if "universe" in kw:
            kw["universe"] = tuple(kw["universe"])
        return cls(**kw)


class EtfMomentum(Strategy):
    name = "etf_momentum"

    def __init__(self, calendar: TradingCalendar | None = None) -> None:
        self._cal = calendar or get_calendar()

    def rebalance_day(self, as_of: date) -> date:
        """as_of 当天或之前最近的月末交易日。"""
        if self._cal.is_month_end(as_of):
            return as_of
        d = self._cal.previous_trading_day(as_of)
        while not self._cal.is_month_end(d):
            d = self._cal.previous_trading_day(d)
        return d

    def target_weights(self, ctx: Context) -> dict[str, float]:
        p = MomentumParams.from_mapping(ctx.params)
        day = self.rebalance_day(ctx.as_of)
        symbols: Sequence[str] = [*p.universe, p.safe_asset]
        prices = ctx.bars(symbols, end=day)
        return select(prices, day, p)


def _momentum(series: pd.Series, lookback: int) -> float | None:
    s = series.dropna()
    if len(s) < lookback + 1:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - lookback] - 1)


def _above_sma(series: pd.Series, window: int) -> bool | None:
    s = series.dropna()
    if len(s) < window:
        return None
    return bool(s.iloc[-1] >= s.iloc[-window:].mean())


def select(prices: pd.DataFrame, day: date, p: MomentumParams) -> dict[str, float]:
    """prices：截至调仓日（含）的含分红复权价宽表。"""
    prices = prices.loc[: pd.Timestamp(day)]
    if prices.empty or pd.Timestamp(prices.index[-1]).date() != day:
        return {}  # 调仓日没有数据，无法计算
    safe = prices[p.safe_asset] if p.safe_asset in prices else pd.Series(dtype=float)
    safe_ret = _momentum(safe, p.lookback)
    if safe_ret is None:
        return {}
    scores = {
        s: r
        for s in p.universe
        if s in prices and (r := _momentum(prices[s], p.lookback)) is not None
    }
    ranked = sorted(scores, key=lambda s: scores[s], reverse=True)[: p.top_k]
    slot = 1.0 / p.top_k
    weights: dict[str, float] = {}
    for s in ranked:
        trend_ok = _above_sma(prices[s], p.sma_window)
        chosen = s if trend_ok and scores[s] >= safe_ret else p.safe_asset
        weights[chosen] = weights.get(chosen, 0.0) + slot
    # 可参与排名的标的不足 top_k 时，剩余份额也放到避险资产
    missing = p.top_k - len(ranked)
    if missing:
        weights[p.safe_asset] = weights.get(p.safe_asset, 0.0) + slot * missing
    return weights
