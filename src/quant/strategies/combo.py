"""组合策略（设计：specs/2026-10-30-momentum-combo-design.md 第 4 节）。

目标权重 = (动量仓 × momentum_weight + V2 仓 × swing_weight) × 择时系数：
- 动量仓：momentum_select（月度选股，各 1 / top_n）；
- V2 仓：swing_pullback 的 V2 参数（120 日线向上、不用 PSQ）；
- 择时：vol_symbol（QQQ）近 vol_window 日年化波动率 > vol_threshold 时系数为 vol_scale，否则为 1。
同一标的在两仓的权重相加。纯计算，两个子策略共用传入的 Context（只替换 params）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import timedelta
from typing import Any, ClassVar

import pandas as pd

from quant.core.calendar import TradingCalendar, get_calendar
from quant.strategies.base import Context, Strategy
from quant.strategies.momentum_select import MomentumParams, MomentumSelect
from quant.strategies.swing_pullback import SwingPullback

TRADING_DAYS = 252
V2_SWING: Mapping[str, Any] = {
    "trend_ma": 120,
    "slope_days": 10,
    "support_ma": 20,
    "confirm": "close_strength",
    "exit_ma": 10,
    "use_inverse": False,
}


@dataclass(frozen=True)
class ComboParams:
    momentum_weight: float = 0.4
    swing_weight: float = 0.6
    vol_symbol: str = "QQQ"
    vol_window: int = 20
    vol_threshold: float = 0.30
    vol_scale: float = 0.5
    momentum: Mapping[str, Any] = field(default_factory=dict)
    swing: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.momentum_weight < 0 or self.swing_weight < 0:
            raise ValueError("仓位比例不能为负")
        if self.momentum_weight + self.swing_weight > 1 + 1e-9:
            raise ValueError(
                f"momentum_weight + swing_weight = {self.momentum_weight + self.swing_weight} > 1"
            )

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> ComboParams:
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(params) - known)
        if unknown:
            raise ValueError(f"未知参数：{unknown}")
        return cls(**params)

    def swing_params(self) -> dict[str, Any]:
        """V2 默认参数，被用户传入的同名键覆盖。"""
        return {**V2_SWING, **self.swing}

    def momentum_params(self) -> dict[str, Any]:
        return dict(self.momentum)


def vol_factor(close: pd.Series, window: int, threshold: float, scale: float) -> float:
    """最近 window 个日收益率的年化波动率 > threshold 时为 scale，否则为 1；数据不足时为 1。"""
    rets = close.pct_change(fill_method=None).iloc[-window:]
    if rets.count() < window:
        return 1.0
    vol = float(rets.std(ddof=1)) * math.sqrt(TRADING_DAYS)
    return scale if vol > threshold else 1.0


def merge_weights(
    momentum: Mapping[str, float],
    swing: Mapping[str, float],
    mw: float,
    sw: float,
    factor: float,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for weights, k in ((momentum, mw), (swing, sw)):
        for s, w in weights.items():
            out[s] = out.get(s, 0.0) + w * k
    return {s: w * factor for s, w in out.items()}


class Combo(Strategy):
    name = "combo"
    # 择时把动量仓 10% → 5%、V2 仓 15% → 7.5%：阈值必须 < 0.05 才会执行减仓；
    # 同时足够大，价格漂移不触发来回调仓（动量仓需涨约 40% 才触发）
    min_weight_diff: ClassVar[float | None] = 0.04
    max_daily_move: ClassVar[float] = 0.6
    uses_universe: ClassVar[bool] = True

    def __init__(self, calendar: TradingCalendar | None = None) -> None:
        cal = calendar or get_calendar()
        self._momentum = MomentumSelect(cal)
        self._swing = SwingPullback(cal)

    def symbols(self, params: Mapping[str, Any]) -> list[str]:
        """固定部分（V2 仓与择时标的）；动量仓的候选池随成分股变化，由调用方另行准备。"""
        p = ComboParams.from_mapping(params)
        syms = self._swing.symbols(p.swing_params())
        return syms if p.vol_symbol in syms else [*syms, p.vol_symbol]

    def earnings_symbols(self, params: Mapping[str, Any]) -> list[str]:
        """V2 仓的个股（动量仓持有过财报，不需要财报日）。"""
        p = ComboParams.from_mapping(params)
        return self._swing.earnings_symbols(p.swing_params()) if p.swing_weight > 0 else []

    def warmup_days(self, params: Mapping[str, Any]) -> int:
        p = ComboParams.from_mapping(params)
        return max(
            self._swing.warmup_days(p.swing_params()),
            self._momentum.warmup_days(p.momentum_params()),
            p.vol_window + 1,
        )

    def target_weights(self, ctx: Context) -> dict[str, float]:
        p = ComboParams.from_mapping(ctx.params)
        MomentumParams.from_mapping(p.momentum_params())  # 提前校验参数
        m = (
            self._momentum.target_weights(replace(ctx, params=p.momentum_params()))
            if p.momentum_weight > 0
            else {}
        )
        s = (
            self._swing.target_weights(replace(ctx, params=p.swing_params()))
            if p.swing_weight > 0
            else {}
        )
        start = ctx.as_of - timedelta(days=int((p.vol_window + 5) * 1.6) + 10)
        close = ctx.field([p.vol_symbol], "close", start)[p.vol_symbol]
        factor = vol_factor(close, p.vol_window, p.vol_threshold, p.vol_scale)
        return merge_weights(m, s, p.momentum_weight, p.swing_weight, factor)
