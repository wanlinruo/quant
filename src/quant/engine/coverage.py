"""候选池数据覆盖：回测区间内各成分快照中“没有数据的成员”占比（研究脚本与服务器回测共用）。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

import pandas as pd

from quant.strategies.universe import Constituents


def missing_ratio_by_year(
    universe: Constituents, have: Collection[str], start: date, end: date
) -> pd.Series:
    """每年各成分快照中“没有数据的成员”占比的平均值（start 当时生效的快照计入 start 所在年）。"""
    days = sorted(universe.snapshots)
    rows: dict[int, list[float]] = {}
    for i, d in enumerate(days):
        nxt = days[i + 1] if i + 1 < len(days) else date.max
        if d > end or nxt <= start:
            continue
        members = universe.snapshots[d]
        ratio = sum(1 for s in members if s not in have) / len(members)
        rows.setdefault(max(d, start).year, []).append(ratio)
    return pd.Series({y: sum(v) / len(v) for y, v in sorted(rows.items())}, dtype=float)
