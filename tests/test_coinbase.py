"""Coinbase 比特币日线（#69 第 2 批 Task 1）。不访问网络。"""

from datetime import date

import pytest

from fakes import FakeFetch
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.coinbase import btc_daily


def test_btc_daily_ascending_utc() -> None:
    rows = [[1793491200, 77370, 78125.91, 77423.98, 77756.72, 1158.7],
            [1793404800, 76954.57, 77645.12, 77180.42, 77423.98, 2434.2]]  # fmt: skip
    fetch = FakeFetch((200, rows))
    got = btc_daily(fetch)
    assert [b.date for b in got] == [date(2026, 10, 31), date(2026, 11, 1)]
    assert got[-1].close == 77756.72 and got[-1].open == 77423.98 and got[-1].low == 77370
    assert "User-Agent" in fetch.calls[0][1]


def test_btc_daily_error() -> None:
    with pytest.raises(DataUnavailableError):
        btc_daily(FakeFetch((503, "down")))
    with pytest.raises(DataUnavailableError):
        btc_daily(FakeFetch(OSError("x")))


def test_btc_daily_drops_unfinished_candle() -> None:
    """第一根是当前 UTC 日、尚未走完的 K 线：丢掉，只用已收盘的日线。"""
    from datetime import UTC, datetime

    rows = [[1793491200, 1, 2, 1, 77756.72, 1158.7], [1793404800, 1, 2, 1, 77423.98, 2434.2]]
    now = datetime(2026, 11, 1, 9, 42, tzinfo=UTC)  # 11-01 那根还没走完
    got = btc_daily(FakeFetch((200, rows)), now=now)
    assert [b.date for b in got] == [date(2026, 10, 31)]
    later = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    assert len(btc_daily(FakeFetch((200, rows)), now=later)) == 2
