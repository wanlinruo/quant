"""拆股调整（组合策略设计 5.2）：新浪日 K 不做拆股调整，排序前用老虎拆股记录换算到最新口径。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd


def split_adjust(bars: pd.DataFrame, splits: Sequence[tuple[date, float]]) -> pd.DataFrame:
    """splits：[(除权日, 价格乘数 from_factor / to_factor)]。

    除权日之前的开高低收乘以乘数、成交量除以乘数（多次拆股累乘）；除权日当天及之后不变。
    返回副本，adj_close = 调整后的收盘价（不含分红）。
    """
    out = bars.copy()
    factor = pd.Series(1.0, index=out.index)
    idx = pd.DatetimeIndex(out.index)
    for ex_date, mult in splits:
        factor[idx < pd.Timestamp(ex_date)] *= mult
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] * factor
    out["volume"] = (out["volume"] / factor).round().astype("int64")
    out["adj_close"] = out["close"]
    return out
