"""数据准备：缺数据时从数据源拉取 → 写入 Parquet → 校验。回测命令与每日数据任务共用。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd

from quant.core.calendar import TradingCalendar
from quant.data.providers.base import DataProvider
from quant.data.store import ParquetStore
from quant.data.validate import Report, validate_bars


class DataValidationError(RuntimeError):
    def __init__(self, report: Report) -> None:
        super().__init__(f"数据校验未通过：\n{report.summary()}")
        self.report = report


def _covers(df: pd.DataFrame, start: date, end: date, cal: TradingCalendar) -> bool:
    if df.empty:
        return False
    first, last = pd.Timestamp(df.index[0]).date(), pd.Timestamp(df.index[-1]).date()
    first_needed = start if cal.is_trading_day(start) else cal.next_trading_day(start)
    return first <= first_needed and last >= end


def ensure_bars(
    store: ParquetStore,
    provider: DataProvider,
    cal: TradingCalendar,
    symbols: Sequence[str],
    start: date,
    end: date,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """保证每个标的在 [start, end] 有数据且通过校验，返回各标的日线。

    本地数据不覆盖区间（或 refresh=True）时从 provider 拉取并合并写入。
    标的上市晚于 start 时，只校验上市之后的部分。
    """
    out: dict[str, pd.DataFrame] = {}
    report = Report()
    for s in symbols:
        df = store.read_bars(s, start, end)
        if refresh or not _covers(df, start, end, cal):
            store.write_bars(s, provider.daily_bars(s, start, end), provider.source)
            df = store.read_bars(s, start, end)
        if df.empty:
            report.extend(validate_bars(s, df, cal, start, end).issues)
            continue
        first = max(start, pd.Timestamp(df.index[0]).date())
        report.extend(validate_bars(s, df, cal, first, end).issues)
        out[s] = df
    if not report.ok:
        raise DataValidationError(report)
    return out
