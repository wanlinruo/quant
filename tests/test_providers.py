from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quant.data.providers.base import BAR_COLUMNS, DataUnavailableError, empty_bars, normalize_bars
from quant.data.providers.yfinance import YFinanceProvider


def yf_frame(dates: list[str], tz: str | None = None) -> pd.DataFrame:
    """模拟 yf.download(auto_adjust=False, multi_level_index=False) 的输出。"""
    n = len(dates)
    close = np.linspace(100, 100 + n - 1, n)
    idx = pd.DatetimeIndex(dates, name="Date")
    if tz:
        idx = idx.tz_localize(tz)
    return pd.DataFrame(
        {
            "Adj Close": close * 0.9,
            "Close": close,
            "High": close + 1,
            "Low": close - 1,
            "Open": close,
            "Volume": np.arange(n, dtype="int64") * 1000,
        },
        index=idx,
    )


class FakeDownloader:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> pd.DataFrame | None:
        self.calls.append(kwargs)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]


def provider(results: list[Any]) -> tuple[YFinanceProvider, FakeDownloader, list[float]]:
    fake, sleeps = FakeDownloader(results), list[float]()
    return YFinanceProvider(downloader=fake, sleep=sleeps.append), fake, sleeps


def test_normalized_output() -> None:
    p, fake, _ = provider([yf_frame(["2026-10-26", "2026-10-27", "2026-10-28"])])
    df = p.daily_bars("SPY", date(2026, 10, 26), date(2026, 10, 28))
    assert tuple(df.columns) == BAR_COLUMNS
    assert df.index.name == "date"
    assert pd.DatetimeIndex(df.index).tz is None
    assert list(df.index) == list(pd.to_datetime(["2026-10-26", "2026-10-27", "2026-10-28"]))
    assert df["adj_close"].iloc[0] == pytest.approx(90.0)
    assert df["close"].iloc[0] == pytest.approx(100.0)
    assert df["volume"].dtype == "int64"
    # 调用参数：不复权的 Close + 单独的 Adj Close；end 需要 +1 天
    call = fake.calls[0]
    assert call["auto_adjust"] is False
    assert call["start"] == "2026-10-26"
    assert call["end"] == "2026-10-29"


def test_retries_then_succeeds() -> None:
    p, fake, sleeps = provider([ConnectionError("SSL EOF"), None, yf_frame(["2026-10-28"])])
    df = p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))
    assert len(df) == 1
    assert len(fake.calls) == 3
    assert sleeps == [2.0, 4.0]


def test_gives_up_after_retries() -> None:
    p, _, sleeps = provider([ConnectionError("a"), ConnectionError("b"), ConnectionError("c")])
    with pytest.raises(DataUnavailableError, match="重试 3 次"):
        p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))
    assert len(sleeps) == 2


def test_format_error_not_retried() -> None:
    bad = yf_frame(["2026-10-28"]).drop(columns=["Open"])
    p, fake, _ = provider([bad])
    with pytest.raises(ValueError, match="缺少列"):
        p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))
    assert len(fake.calls) == 1


def test_invalid_range() -> None:
    p, _, _ = provider([])
    with pytest.raises(ValueError):
        p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 27))


def test_normalize_tz_dedup_sort_and_clip() -> None:
    raw = yf_frame(["2026-10-28", "2026-10-26", "2026-10-27", "2026-10-27"], tz="America/New_York")
    raw = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                              "Adj Close": "adj_close", "Volume": "volume"})  # fmt: skip
    df = normalize_bars(raw, start=date(2026, 10, 27))
    assert pd.DatetimeIndex(df.index).tz is None
    assert list(df.index) == list(pd.to_datetime(["2026-10-27", "2026-10-28"]))
    assert df.index.is_unique and df.index.is_monotonic_increasing


def test_normalize_without_adj_close() -> None:
    raw = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [10]},
        index=pd.DatetimeIndex(["2026-10-28"]),
    )
    df = normalize_bars(raw)
    assert np.isnan(df["adj_close"].iloc[0])


def test_empty_bars_schema() -> None:
    df = empty_bars()
    assert tuple(df.columns) == BAR_COLUMNS
    assert len(df) == 0


def test_yfinance_maps_dot_ticker() -> None:
    # 标普成分名单用 BRK.B，Yahoo 用 BRK-B
    p, fake, _ = provider([yf_frame(["2024-01-02"])])
    df = p.daily_bars("BRK.B", date(2024, 1, 2), date(2024, 1, 2))
    assert [c["tickers"] for c in fake.calls] == ["BRK-B"]
    assert len(df) == 1
