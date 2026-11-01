"""Forex Factory 每周经济日历（#69 第 2 批 Task 2）。不访问网络。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fakes import FakeFetch
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.forexfactory import this_week


def test_ff_parse_converts_offset_to_et() -> None:
    body = [{"title": "Non-Farm Employment Change", "country": "USD",
             "date": "2026-11-06T08:30:00-05:00", "impact": "High", "forecast": "63K",
             "previous": "29K"},
            {"title": "Daylight Saving Time Shift", "country": "USD",
             "date": "2026-11-01T02:00:00-05:00", "impact": "Holiday", "forecast": "",
             "previous": ""},
            {"title": "German Prelim CPI", "country": "EUR", "date": "2026-11-02T08:00:00-05:00",
             "impact": "High", "forecast": "", "previous": ""}]  # fmt: skip
    got = this_week(FakeFetch((200, body)))
    assert [e.title for e in got] == ["Non-Farm Employment Change", "Daylight Saving Time Shift"]
    assert got[0].time == datetime(2026, 11, 6, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    assert got[0].impact == 3 and got[1].impact == 0 and got[1].time.hour == 2
    assert got[0].forecast == "63K" and got[0].previous == "29K"


def test_ff_dst_boundary() -> None:
    """夏令时结束当晚：02:00 EDT（-04:00）即 01:00 EST。"""
    body = [{"title": "X", "country": "USD", "date": "2026-11-01T02:00:00-04:00", "impact": "Low"}]
    t = this_week(FakeFetch((200, body)))[0].time
    assert (t.hour, t.utcoffset().total_seconds()) == (1, -5 * 3600)  # type: ignore[union-attr]


def test_ff_not_json_is_unavailable() -> None:
    with pytest.raises(DataUnavailableError):
        this_week(FakeFetch((200, "<html>")))
    with pytest.raises(DataUnavailableError):
        this_week(FakeFetch((404, "<html>")))
