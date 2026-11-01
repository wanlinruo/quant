"""新闻流与异动原因（#69 第 2 批 Task 4）。"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from quant.data.providers.finnhub import NewsRaw
from quant.market.news import build_feed, pick_reason, short_name

T0 = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)  # 美东 16:00


def n(h: str, mins: int = 0, i: str | None = None, rel: Sequence[str] = ()) -> NewsRaw:
    return NewsRaw(i or h, T0 + timedelta(minutes=mins), h, "Reuters", f"https://n/{i or h}",
                   tuple(rel))  # fmt: skip


def test_short_name() -> None:
    assert short_name("Apple Inc.") == "Apple"
    assert short_name("Exxon Mobil Corp.") == "Exxon"
    assert short_name("3M") is None and short_name(None) is None


def test_pick_reason_requires_symbol_or_name_and_window() -> None:
    items = [n("5 Best High Growth Stocks", 30), n("Apple beats on iPhone sales", 10),
             n("AAPL old news", -24 * 60), n("Pineapple prices jump", 40)]  # fmt: skip
    r = pick_reason("AAPL", "Apple Inc.", items, since=T0 - timedelta(hours=20))
    assert r is not None and r.headline == "Apple beats on iPhone sales"
    assert r.source == "Reuters" and r.url == "https://n/Apple beats on iPhone sales"
    assert pick_reason("AAPL", "Apple Inc.", items[:1], since=T0 - timedelta(hours=20)) is None


def test_pick_reason_latest_wins() -> None:
    items = [n("NVDA rises", 5), n("Nvidia extends gains", 50)]
    r = pick_reason("NVDA", "Nvidia", items, since=T0)
    assert r is not None and r.headline == "Nvidia extends gains"


def test_feed_categories_and_tags() -> None:
    market = [n("Fed holds rates", 1, "m1")]
    company = {"XOM": [n("Exxon Q3 earnings beat", 2, "c1"), n("Exxon names CFO", 3, "c2")],
               "NVDA": [n("Nvidia rallies", 4, "c3")]}  # fmt: skip
    feed = build_feed(market, company, holdings={"XOM"}, since=T0 - timedelta(days=2),
                      names={"XOM": "Exxon Mobil Corp."})  # fmt: skip
    by = {f.headline: f for f in feed}
    assert by["Fed holds rates"].category == "macro" and by["Fed holds rates"].tags == []
    assert by["Exxon Q3 earnings beat"].category == "earnings"
    assert by["Exxon Q3 earnings beat"].tags == ["XOM"]
    assert by["Exxon names CFO"].category == "company"
    assert "Nvidia rallies" not in by  # 非持仓的公司新闻只用于原因列
    assert [f.time for f in feed] == sorted((f.time for f in feed), reverse=True)


def test_feed_dedup_and_limit() -> None:
    market = [n("Same", 1, "a"), n("same", 2, "b")] + [
        n(f"h{i}", i + 10, f"x{i}") for i in range(100)
    ]
    feed = build_feed(market, {}, set(), since=T0 - timedelta(days=2), limit=80)
    assert len(feed) == 80 and sum(f.headline.lower() == "same" for f in feed) <= 1
    assert feed[0].time > feed[-1].time


def test_feed_drops_old_and_caps_per_symbol() -> None:
    company = {"XOM": [n(f"Exxon {i}", i) for i in range(8)] + [n("Exxon ancient", -5000)]}
    feed = build_feed([], company, {"XOM"}, since=T0 - timedelta(days=2), names={"XOM": "Exxon"})
    assert len(feed) == 5 and all("ancient" not in f.headline for f in feed)
    assert feed[0].headline == "Exxon 7"


def test_holdings_feed_only_relevant() -> None:
    noise = n("5 Best High Growth Stocks Outside Big Tech", 1, "a")
    company = {"AAPL": [noise, n("Apple beats on iPhone sales", 2, "b"), n("AAPL slips", 3, "c")]}
    feed = build_feed(
        [], company, {"AAPL"}, since=T0 - timedelta(days=2), names={"AAPL": "Apple Inc."}
    )
    assert [f.headline for f in feed] == ["AAPL slips", "Apple beats on iPhone sales"]


# ---- 终审修复（#79）：相关性匹配 ----

from quant.market.news import mentions  # noqa: E402


def test_mentions_punctuated_names() -> None:
    assert mentions("McDonald's beats estimates", "MCD", "McDonald's")
    assert mentions("Coca-Cola raises outlook", "KO", "Coca-Cola Company (The)")
    assert mentions("Lowe's shares slip", "LOW", "Lowe's")


def test_mentions_ticker_case_and_short_tickers() -> None:
    assert not mentions("5 Best Stocks to Buy for a Rally", "A", "Agilent Technologies")
    assert not mentions("Why It Pays to Wait", "IT", "Gartner")
    assert not mentions("Markets Turn On Fed Comments", "ON", "ON Semiconductor")
    assert mentions("Agilent (A) tops estimates", "A", "Agilent Technologies")
    assert mentions("Ford (F) recalls trucks", "F", "Ford Motor Company")
    assert mentions("NVDA rallies", "NVDA", "Nvidia")
    assert not mentions("nvda lower", "NVDA", None)
    assert mentions("GE Aerospace jumps", "GE", "GE Aerospace") and mentions("KO beats", "KO", None)


def test_mentions_generic_first_word_and_aliases() -> None:
    assert not mentions("American Airlines cuts routes", "AXP", "American Express")
    assert mentions("American Express card spending rises", "AXP", "American Express")
    assert not mentions("Home prices climb", "HD", "Home Depot (The)")
    assert mentions("Disney parks revenue jumps", "DIS", "Walt Disney Company (The)")
    assert mentions("Google unveils new chip", "GOOGL", "Alphabet Inc. (Class A)")
    assert mentions("3M settles lawsuit", "MMM", "3M")
