"""Strategy 抽象接口与 Context（PROJECT_PLAN 6.2）。

策略只输出目标仓位权重，纯计算，不做 I/O、不下单、不读写数据库。
回测与实盘使用同一个策略类，只是注入的 Context 数据来源不同。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from quant.strategies.events import EventCalendar
from quant.strategies.universe import Constituents

# 数据加载函数：(symbols, start, end) → 以日期为索引的 DataFrame，包含 end 当日
BarLoader = Callable[[Sequence[str], date | None, date], pd.DataFrame]
# 指定字段的加载函数：(symbols, field, start, end) → 宽表；field 为 open/high/low/close/volume，
# 价格为含分红复权口径（与回测撮合一致）
FieldLoader = Callable[[Sequence[str], str, date | None, date], pd.DataFrame]
FIELDS = ("open", "high", "low", "close", "volume")

WEIGHT_TOLERANCE = 1e-9


class LookAheadError(ValueError):
    """请求了 as_of 之后的数据。"""


def _truncate(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """只保留索引日期 ≤ as_of 的行。索引必须是日期。"""
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is not None:
            raise ValueError("bars 索引应为交易日（不带时区）")
        mask = idx.normalize() <= pd.Timestamp(as_of)
    elif len(idx) == 0 or all(isinstance(d, date) for d in idx):
        mask = np.array([pd.Timestamp(d).date() <= as_of for d in idx], dtype=bool)
    else:
        raise TypeError("bars 索引必须是日期")
    return df.loc[mask].copy()


@dataclass(frozen=True)
class Context:
    """策略运行上下文。

    as_of 为信号日（收盘后）。bars() 在实现上强制截断到 as_of：
    请求 end > as_of 直接报错；即使数据源返回了更晚的数据，也会被丢弃。
    """

    as_of: date
    loader: BarLoader = field(repr=False)
    params: Mapping[str, Any] = field(default_factory=dict)
    field_loader: FieldLoader | None = field(default=None, repr=False)
    events: EventCalendar = field(default_factory=EventCalendar)
    # 指数成分股快照（需要“当时成分股”的策略使用）；按日期查询，不需要截断
    universe: Constituents | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        # 包装原始 loader：即使策略绕过 bars() 直接调用 ctx.loader，也拿不到 as_of 之后的数据
        raw, as_of = self.loader, self.as_of

        def guarded(symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
            end = min(end, as_of)
            return _truncate(raw(list(symbols), start, end), end)

        object.__setattr__(self, "loader", guarded)
        raw_fields = self.field_loader
        if raw_fields is not None:

            def guarded_fields(
                symbols: Sequence[str], name: str, start: date | None, end: date
            ) -> pd.DataFrame:
                end = min(end, as_of)
                return _truncate(raw_fields(list(symbols), name, start, end), end)

            object.__setattr__(self, "field_loader", guarded_fields)

    def bars(
        self, symbols: Sequence[str], start: date | None = None, end: date | None = None
    ) -> pd.DataFrame:
        end = self.as_of if end is None else end
        if end > self.as_of:
            raise LookAheadError(f"请求截至 {end} 的数据，晚于信号日 {self.as_of}")
        if start is not None and start > end:
            raise ValueError(f"start {start} 晚于 end {end}")
        return self.loader(symbols, start, end)

    def field(
        self,
        symbols: Sequence[str],
        name: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """指定字段（open/high/low/close/volume）的宽表，同样截断到 as_of。"""
        if self.field_loader is None:
            raise ValueError("当前数据源不提供开高低收量（Context.field_loader 未设置）")
        if name not in FIELDS:
            raise ValueError(f"非法字段：{name}")
        end = self.as_of if end is None else end
        if end > self.as_of:
            raise LookAheadError(f"请求截至 {end} 的数据，晚于信号日 {self.as_of}")
        return self.field_loader(symbols, name, start, end)


def validate_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """权重必须是有限非负数，和不超过 1（剩余为现金，不用杠杆）。返回去掉 0 权重后的副本。"""
    out: dict[str, float] = {}
    for sym, w in weights.items():
        if not isinstance(w, int | float) or isinstance(w, bool) or not math.isfinite(w):
            raise ValueError(f"{sym} 权重非法：{w!r}")
        if w < 0:
            raise ValueError(f"{sym} 权重为负（不做空）：{w}")
        if w > 0:
            out[sym] = float(w)
    total = sum(out.values())
    if total > 1 + WEIGHT_TOLERANCE:
        raise ValueError(f"权重和 {total} 超过 1")
    return out


class Strategy(ABC):
    name: ClassVar[str]
    # 非 None 时替换订单计算的 min_weight_diff：持有型策略用较大的值，避免价格漂移触发来回调仓
    min_weight_diff: ClassVar[float | None] = None
    # 数据校验的单日涨跌阈值：超过即视为可疑数据（如未复权的拆股），停止运行
    max_daily_move: ClassVar[float] = 0.15

    @abstractmethod
    def target_weights(self, ctx: Context) -> dict[str, float]:
        """返回目标仓位权重，如 {"QQQ": 0.6, "TLT": 0.4}；权重和 ≤ 1，剩余为现金。"""

    def symbols(self, params: Mapping[str, Any]) -> list[str]:
        """该参数下需要的全部标的（用于准备数据）。"""
        raise NotImplementedError(f"{type(self).__name__} 未声明所需标的")

    def warmup_days(self, params: Mapping[str, Any]) -> int:
        """回测开始前需要的历史交易日数。"""
        return 0

    def run(self, ctx: Context) -> dict[str, float]:
        """调用方使用的入口：计算并校验权重。"""
        return validate_weights(self.target_weights(ctx))
