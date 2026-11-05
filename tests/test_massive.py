"""Massive 客户端（#69 第 1 批 Task 1）。不访问网络。"""

from datetime import date
from typing import Any

import pytest
from pydantic import SecretStr

from fakes import FakeFetch
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.massive import MassiveAuthError, MassiveClient, MassiveRateLimited

KEY = SecretStr("mk-secret-1")


class Clock:
    def __init__(self) -> None:
        self.t = 100.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def client(fetch: FakeFetch, clock: Clock | None = None) -> MassiveClient:
    c = clock or Clock()
    return MassiveClient(KEY, fetch, sleep=c.sleep, clock=c.now)


def bar(t: str, o: float, h: float, lo: float, c: float) -> dict[str, Any]:
    return {"T": t, "o": o, "h": h, "l": lo, "c": c, "v": 1e6, "vw": c, "t": 1}


GROUPED = {"status": "OK", "results": [
    bar("SPY", 761.38, 761.46, 750.36, 755.72), bar("BRK.B", 549.88, 550.47, 542.32, 542.49),
]}  # fmt: skip


def test_grouped_daily_maps_fields() -> None:
    df = client(FakeFetch((200, GROUPED))).grouped_daily(date(2026, 10, 30))
    assert list(df.columns) == ["symbol", "open", "high", "low", "close", "volume", "vwap"]
    assert df.set_index("symbol").loc["BRK.B", "close"] == 542.49
    assert len(df) == 2


def test_empty_day_returns_empty_frame() -> None:
    df = client(FakeFetch((200, {"status": "OK", "resultsCount": 0}))).grouped_daily(
        date(2026, 11, 1)
    )
    assert df.empty and "close" in df.columns


def test_auth_header_not_in_url() -> None:
    fetch = FakeFetch((200, GROUPED))
    client(fetch).grouped_daily(date(2026, 10, 30))
    url, headers = fetch.calls[0]
    assert (
        url
        == "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-10-30?adjusted=true"
    )
    assert headers["Authorization"] == "Bearer mk-secret-1" and "mk-secret" not in url


def test_rate_limit_spacing() -> None:
    clock = Clock()
    c = client(FakeFetch((200, GROUPED)), clock)
    c.grouped_daily(date(2026, 10, 29))
    clock.t += 2.5
    c.grouped_daily(date(2026, 10, 30))
    assert clock.slept == [pytest.approx(10.0)]


def test_429_stops_and_raises_rate_limited() -> None:
    with pytest.raises(MassiveRateLimited):
        client(FakeFetch((429, {"status": "ERROR"}))).grouped_daily(date(2026, 10, 30))


def test_401_raises_auth() -> None:
    with pytest.raises(MassiveAuthError):
        client(FakeFetch((401, {"status": "ERROR"}))).grouped_daily(date(2026, 10, 30))


def test_ticker_details() -> None:
    body = {
        "results": {
            "name": "Apple Inc.",
            "market_cap": 5.0e12,
            "sic_description": "ELECTRONIC COMPUTERS",
        }
    }
    d = client(FakeFetch((200, body))).ticker_details("AAPL")
    assert d is not None and d.name == "Apple Inc." and d.market_cap == 5.0e12
    assert client(FakeFetch((404, {}))).ticker_details("ZZZZ") is None


def test_key_not_in_error_message() -> None:
    err = OSError("https://api.massive.com/x Authorization: Bearer mk-secret-1")
    with pytest.raises(DataUnavailableError) as e:
        client(FakeFetch(err)).grouped_daily(date(2026, 10, 30))
    assert "mk-secret-1" not in str(e.value) and e.value.__suppress_context__
    with pytest.raises(DataUnavailableError) as e:
        client(FakeFetch((500, {"error": "x"}))).grouped_daily(date(2026, 10, 30))
    assert not isinstance(e.value, MassiveRateLimited | MassiveAuthError)


# ---- 最终审查修复（#69）----


def test_403_is_not_auth_error() -> None:
    """免费档对超出套餐的时间范围（如当日数据未就绪）返回 403：按暂时拿不到处理，非 key 无效。"""
    body = {"status": "NOT_AUTHORIZED", "message": "Your plan doesn't include this data timeframe."}
    with pytest.raises(DataUnavailableError) as e:
        client(FakeFetch((403, body))).grouped_daily(date(2026, 10, 30))
    assert not isinstance(e.value, MassiveAuthError | MassiveRateLimited)
    assert "HTTP 403" in str(e.value) and "美东跨日" in str(e.value)


def test_403_does_not_echo_response_secrets() -> None:
    with pytest.raises(DataUnavailableError) as err:
        client(FakeFetch((403, {"message": "mk-secret-1"}))).grouped_daily(date(2026, 10, 30))
    assert "mk-secret-1" not in str(err.value)


def test_splits() -> None:
    body = {
        "results": [
            {"ticker": "NVDA", "execution_date": "2026-10-30", "split_from": 1, "split_to": 10}
        ]
    }
    fetch = FakeFetch((200, body))
    got = client(fetch).splits(date(2026, 10, 30))
    assert (
        fetch.calls[0][0]
        == "https://api.massive.com/v3/reference/splits?execution_date=2026-10-30&limit=1000"
    )
    assert len(got) == 1 and got[0].symbol == "NVDA" and got[0].factor == pytest.approx(0.1)
    assert client(FakeFetch((200, {"results": []}))).splits(date(2026, 10, 29)) == []


def test_shared_request_limit_across_instances(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    clock = [1000.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", sleep)
    path = tmp_path / ".massive-rate"
    for _ in range(2):
        MassiveClient(KEY, FakeFetch((200, {"results": []})), rate_limit_path=path).grouped_daily(
            date(2026, 11, 2)
        )
    assert waits == [pytest.approx(12.5)]
    assert KEY.get_secret_value() not in path.read_text()


def test_stop_prevents_massive_requests() -> None:
    fetch = FakeFetch((200, {"results": []}))
    with pytest.raises(DataUnavailableError, match="采集已停止"):
        MassiveClient(KEY, fetch, stop=lambda: True).grouped_daily(date(2026, 11, 2))
    assert not fetch.calls
