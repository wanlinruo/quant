"""区间收益（个股页与首页板块 ETF 共用的口径）。纯计算，只依赖 pandas。"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd


def _num(x: float | None) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


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
