"""事件日历（swing_pullback 设计第 6 节）：财报反应日与 FOMC 决议日。纯数据，策略通过 Context 读取。

- earnings：每个标的的财报“反应日”（市场首次对财报做出反应的交易日；盘后公布则为下一交易日），升序。
- fomc：FOMC 例会决议公布日，升序。
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType


@dataclass(frozen=True)
class EventCalendar:
    earnings: Mapping[str, tuple[date, ...]] = field(default_factory=dict)
    fomc: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        earnings = {s: tuple(sorted(set(ds))) for s, ds in self.earnings.items()}
        object.__setattr__(self, "earnings", MappingProxyType(earnings))
        object.__setattr__(self, "fomc", tuple(sorted(set(self.fomc))))
        object.__setattr__(self, "_fomc_set", frozenset(self.fomc))

    def __reduce__(
        self,
    ) -> tuple[type[EventCalendar], tuple[dict[str, tuple[date, ...]], tuple[date, ...]]]:
        # MappingProxyType 不能 pickle；多进程回测需要传给子进程
        return (EventCalendar, (dict(self.earnings), self.fomc))

    def next_earnings(self, symbol: str, after: date) -> date | None:
        """第一个晚于 after 的反应日；没有记录时返回 None。"""
        days = self.earnings.get(symbol, ())
        i = bisect.bisect_right(days, after)
        return days[i] if i < len(days) else None

    def is_fomc(self, day: date) -> bool:
        return day in self._fomc_set  # type: ignore[attr-defined]
