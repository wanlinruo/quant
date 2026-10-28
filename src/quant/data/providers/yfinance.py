"""yfinance 数据提供者：回测历史日线（PROJECT_PLAN 7.1）。

yfinance 不稳定（Phase 0 实测有 SSL 断连），必须带重试；拉到的数据还要经过 data/validate.py 校验。
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, ClassVar

import pandas as pd

from quant.data.providers.base import DataProvider, DataUnavailableError, normalize_bars

Downloader = Callable[..., pd.DataFrame | None]

_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def _yf_download(**kwargs: Any) -> pd.DataFrame | None:
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # yfinance 内部的 pandas 弃用警告
        result: pd.DataFrame | None = yf.download(**kwargs)
    return result


class YFinanceProvider(DataProvider):
    source: ClassVar[str] = "yfinance"

    def __init__(
        self,
        retries: int = 3,
        backoff_seconds: float = 2.0,
        downloader: Downloader = _yf_download,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retries < 1:
            raise ValueError("retries 至少为 1")
        self._retries = retries
        self._backoff = backoff_seconds
        self._download = downloader
        self._sleep = sleep

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if start > end:
            raise ValueError(f"start {start} 晚于 end {end}")
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                raw = self._download(
                    tickers=symbol,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),  # yfinance 的 end 不含当天
                    interval="1d",
                    auto_adjust=False,
                    actions=False,
                    progress=False,
                    multi_level_index=False,
                    threads=False,
                )
                if raw is None or raw.empty:
                    raise DataUnavailableError(f"yfinance 返回为空：{symbol} {start}~{end}")
            except Exception as e:  # 网络、SSL、限流等，统一重试
                last_error = e
                if attempt < self._retries:
                    self._sleep(self._backoff * attempt)
                continue
            # 格式问题属于代码或数据源变更，不重试，直接抛出
            return normalize_bars(raw.rename(columns=_RENAME), start, end)
        raise DataUnavailableError(
            f"yfinance 拉取 {symbol} 失败（重试 {self._retries} 次）：{last_error}"
        ) from last_error
