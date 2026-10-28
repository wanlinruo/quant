"""DataProvider 接口与统一的日线格式（PROJECT_PLAN 7.1）。

统一格式（所有提供者输出相同结构）：
- 索引：名为 date 的 DatetimeIndex（不带时区、按日、升序、无重复），表示美东交易日。
- 列：open, high, low, close, adj_close（float64），volume（int64）。
  - open/high/low/close：只做拆股调整，不含分红（与老虎“前复权”一致，见 Phase 0 报告 0.2）。
  - adj_close：拆股 + 分红复权，回测和信号一律用它计算收益（Phase 0 报告 0.3）。
    数据源不提供时为 NaN。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import ClassVar

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close", "adj_close")
BAR_COLUMNS = (*PRICE_COLUMNS, "volume")


class DataUnavailableError(RuntimeError):
    """数据源暂时拿不到数据（网络错误、返回为空等）。"""


class DataProvider(ABC):
    source: ClassVar[str]

    @abstractmethod
    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """[start, end]（含两端）的日线，统一格式；没有数据时抛出 DataUnavailableError。"""


def empty_bars() -> pd.DataFrame:
    df = pd.DataFrame(
        {c: pd.Series(dtype="float64") for c in PRICE_COLUMNS}
        | {"volume": pd.Series(dtype="int64")},
        index=pd.DatetimeIndex([], name="date"),
    )
    return df


def normalize_bars(
    df: pd.DataFrame, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    """把提供者的原始结果整理成统一格式，并裁剪到 [start, end]。"""
    missing = [c for c in BAR_COLUMNS if c not in df.columns and c != "adj_close"]
    if missing:
        raise ValueError(f"缺少列：{missing}")
    out = df.copy()
    if "adj_close" not in out.columns:
        out["adj_close"] = np.nan
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    out.index = idx.normalize().astype("datetime64[ns]").rename("date")
    out = out[list(BAR_COLUMNS)]
    out[list(PRICE_COLUMNS)] = out[list(PRICE_COLUMNS)].astype("float64")
    out["volume"] = out["volume"].fillna(0).astype("int64")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if start is not None:
        out = out[out.index >= pd.Timestamp(start)]
    if end is not None:
        out = out[out.index <= pd.Timestamp(end)]
    return out
