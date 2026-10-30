"""指数成分股快照（组合策略设计第 4.2 节）：策略按日期查询“当时”的成分股。纯数据，经 Context 注入。

snapshots：生效日期 → 当日起的完整成分名单。某日的成分股为生效日期 ≤ 该日的最近一条。
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Constituents:
    snapshots: Mapping[date, frozenset[str]]
    _days: tuple[date, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.snapshots:
            raise ValueError("成分股快照为空")
        snaps = {d: frozenset(s) for d, s in self.snapshots.items()}
        object.__setattr__(self, "snapshots", snaps)
        object.__setattr__(self, "_days", tuple(sorted(snaps)))

    def _index(self, d: date) -> int:
        i = bisect.bisect_right(self._days, d) - 1
        if i < 0:
            raise ValueError(f"{d} 早于第一条成分股快照 {self._days[0]}")
        return i

    def members(self, d: date) -> frozenset[str]:
        return self.snapshots[self._days[self._index(d)]]

    def all_symbols(self, start: date, end: date) -> frozenset[str]:
        """[start, end] 内任一时点的成分股的并集（含 start 当时生效的快照）。"""
        lo = max(bisect.bisect_right(self._days, start) - 1, 0)
        hi = bisect.bisect_right(self._days, end)
        out: set[str] = set()
        for d in self._days[lo:hi]:
            out |= self.snapshots[d]
        return frozenset(out)
