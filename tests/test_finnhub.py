"""Finnhub 客户端（#69 第 2 批 Task 2）。不访问网络。"""

from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeFetch
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.finnhub import EarningsRaw, FinnhubAuthError, FinnhubClient, NewsRaw


def client(fetch: FakeFetch) -> FinnhubClient:
    return FinnhubClient(SecretStr("k"), fetch, sleep=lambda s: None)


def test_market_news_parses() -> None:
    body = [{"category": "business", "datetime": 1793526131, "headline": "OPEC+ steady",
             "id": 8705821, "related": "", "source": "Reuters", "summary": "x",
             "url": "https://n/1"}]  # fmt: skip
    fetch = FakeFetch((200, body))
    got = client(fetch).market_news()
    assert fetch.calls[0][0] == "https://finnhub.io/api/v1/news?category=general"
    assert fetch.calls[0][1]["X-Finnhub-Token"] == "k"
    t = datetime.fromtimestamp(1793526131, UTC)
    assert got == [NewsRaw("8705821", t, "OPEC+ steady", "Reuters", "https://n/1", ())]


def test_company_news_url_and_related() -> None:
    body = [{"datetime": "1793482766", "headline": "h", "id": 1, "related": "AAPL,MSFT",
             "source": "Yahoo", "url": "u"}]  # fmt: skip
    fetch = FakeFetch((200, body))
    got = client(fetch).company_news("BRK.B", date(2026, 10, 29), date(2026, 10, 30))
    assert fetch.calls[0][0] == (
        "https://finnhub.io/api/v1/company-news?symbol=BRK.B&from=2026-10-29&to=2026-10-30"
    )
    assert got[0].related == ("AAPL", "MSFT")


def test_earnings_day() -> None:
    body = {"earningsCalendar": [{"symbol": "XOM", "date": "2026-10-30", "hour": "bmo",
                                  "epsEstimate": 1.88, "epsActual": 1.95, "quarter": 3,
                                  "year": 2026}]}  # fmt: skip
    fetch = FakeFetch((200, body))
    got = client(fetch).earnings(date(2026, 10, 30))
    assert "from=2026-10-30&to=2026-10-30" in fetch.calls[0][0]
    assert got == [EarningsRaw("XOM", date(2026, 10, 30), "bmo", 1.88, 1.95)]


def test_401_auth_403_unavailable() -> None:
    with pytest.raises(FinnhubAuthError):
        client(FakeFetch((401, {"error": "Invalid API key"}))).market_news()
    with pytest.raises(DataUnavailableError) as e:
        client(FakeFetch((403, {"error": "premium"}))).market_news()
    assert not isinstance(e.value, FinnhubAuthError)


def test_malformed_items_skipped() -> None:
    ok = {"datetime": 1793526131, "headline": "ok", "id": 2, "source": "CNBC", "url": "u"}
    body = [{"headline": "no time"}, ok]
    assert [n.headline for n in client(FakeFetch((200, body))).market_news()] == ["ok"]
