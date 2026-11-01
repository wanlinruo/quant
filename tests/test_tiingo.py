"""Tiingo 数据提供者（#66 Task 1）。不访问网络。"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from quant.core.settings import TiingoSettings
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.tiingo import TiingoProvider
from quant.data.quota import QuotaExceeded, QuotaLedger

KEY = SecretStr("secret-key-123")


def row(
    d: str, close: float, adj: float, split: float = 1.0, div: float = 0.0, vol: int = 1000
) -> dict[str, Any]:
    return {
        "date": f"{d}T00:00:00.000Z", "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": vol, "adjClose": adj, "divCash": div, "splitFactor": split,
    }  # fmt: skip


class FakeFetch:
    def __init__(self, status: int = 200, body: Any = None, exc: Exception | None = None) -> None:
        self.status, self.body, self.exc = status, body, exc
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        self.calls.append((url, dict(headers)))
        if self.exc:
            raise self.exc
        return self.status, self.body


def provider(tmp_path: Path, fetch: FakeFetch) -> TiingoProvider:
    ledger = QuotaLedger(
        tmp_path / "q.json", TiingoSettings(), lambda: datetime(2026, 11, 2, 15, tzinfo=UTC)
    )
    return TiingoProvider(KEY, ledger, fetch)


def test_maps_fields_and_split_adjusts(tmp_path: Path) -> None:
    fetch = FakeFetch(body=[
        row("2020-08-27", 500.04, 121.16),
        row("2020-08-28", 499.23, 120.97),
        row("2020-08-31", 129.04, 125.07, split=4.0),
    ])  # fmt: skip
    df = provider(tmp_path, fetch).daily_bars("AAPL", date(2020, 8, 27), date(2020, 8, 31))
    assert list(df.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert df["close"].tolist() == pytest.approx([125.01, 124.8075, 129.04])
    assert df.loc["2020-08-28", "high"] == pytest.approx(500.23 / 4)
    assert df["volume"].tolist() == [4000, 4000, 1000]
    assert df["adj_close"].tolist() == pytest.approx([121.16, 120.97, 125.07])
    assert str(df.index[0].date()) == "2020-08-27"


def test_auth_header_not_in_url(tmp_path: Path) -> None:
    fetch = FakeFetch(body=[row("2026-10-30", 10, 10)])
    provider(tmp_path, fetch).daily_bars("SPY", date(2026, 10, 30), date(2026, 10, 30))
    url, headers = fetch.calls[0]
    assert "secret-key-123" not in url
    assert url.startswith("https://api.tiingo.com/tiingo/daily/SPY/prices?")
    assert "startDate=2026-10-30" in url and "endDate=2026-10-30" in url
    assert headers["Authorization"] == "Token secret-key-123"


@pytest.mark.parametrize(("status", "body"), [(200, []), (404, {"detail": "Not found."})])
def test_empty_or_404_raises_unavailable(tmp_path: Path, status: int, body: Any) -> None:
    p = provider(tmp_path, FakeFetch(status=status, body=body))
    with pytest.raises(DataUnavailableError) as e:
        p.daily_bars("ZZZZ", date(2026, 1, 2), date(2026, 10, 30))
    assert not isinstance(e.value, QuotaExceeded)


def test_429_marks_hour_exhausted(tmp_path: Path) -> None:
    p = provider(tmp_path, FakeFetch(status=429, body={"detail": "limit"}))
    with pytest.raises(QuotaExceeded) as e:
        p.daily_bars("SPY", date(2026, 1, 2), date(2026, 10, 30))
    assert e.value.window == "hour"
    with pytest.raises(QuotaExceeded):  # 台账已记为用尽，不再发请求
        p.daily_bars("QQQ", date(2026, 1, 2), date(2026, 10, 30))


def test_requests_counted_in_ledger(tmp_path: Path) -> None:
    p = provider(tmp_path, FakeFetch(body=[row("2026-10-30", 10, 10)]))
    p.daily_bars("SPY", date(2026, 10, 30), date(2026, 10, 30))
    p.daily_bars("SPY", date(2026, 10, 30), date(2026, 10, 30))
    u = p.ledger.usage()
    assert u.hour_used == 2 and u.month_symbols == 1


def test_key_not_in_error_message(tmp_path: Path) -> None:
    err = OSError("failed https://api.tiingo.com/x?token=secret-key-123 Token secret-key-123")
    p = provider(tmp_path, FakeFetch(exc=err))
    with pytest.raises(DataUnavailableError) as e:
        p.daily_bars("SPY", date(2026, 1, 2), date(2026, 10, 30))
    assert "secret-key-123" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


# ---- 最终审查修复（#66）----


def test_dot_symbol_uses_dash(tmp_path: Path) -> None:
    fetch = FakeFetch(body=[row("2026-10-30", 10, 10)])
    provider(tmp_path, fetch).daily_bars("BRK.B", date(2026, 10, 30), date(2026, 10, 30))
    assert "/daily/BRK-B/prices?" in fetch.calls[0][0]


def test_error_kinds(tmp_path: Path) -> None:
    from quant.data.providers.tiingo import SymbolNotFound, TiingoAuthError

    def get(fetch: FakeFetch) -> None:
        provider(tmp_path, fetch).daily_bars("SPY", date(2026, 1, 2), date(2026, 1, 5))

    with pytest.raises(SymbolNotFound):
        get(FakeFetch(status=404, body={}))
    with pytest.raises(TiingoAuthError):
        get(FakeFetch(status=401, body={}))
    for status in (500, 502):
        with pytest.raises(DataUnavailableError) as e:
            get(FakeFetch(status=status, body={}))
        assert not isinstance(e.value, SymbolNotFound | TiingoAuthError)
    with pytest.raises(DataUnavailableError) as e:
        get(FakeFetch(exc=TimeoutError()))
    assert not isinstance(e.value, SymbolNotFound)
