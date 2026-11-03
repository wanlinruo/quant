"""每日任务 market_extras（#69 第 2 批 Task 6）。假客户端与固定样本，不访问网络。"""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant.core.calendar import get_calendar
from quant.data import market_store as ms
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.coinbase import URL as COINBASE_URL
from quant.data.providers.finnhub import EarningsRaw, FinnhubAuthError, NewsRaw
from quant.data.providers.forexfactory import URL as FF_URL
from quant.data.providers.fred import FredUnreachable, Obs
from quant.market.extras_job import run_market_extras

CAL = get_calendar()
DAY = date(2026, 11, 6)  # 周五，非农公布日
NOW = datetime(2026, 11, 7, 2, 0, tzinfo=UTC)  # 美东 11-06 21:00
T_NEWS = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)


class FakeFred:
    """fail：连不上（FredUnreachable）；bad：单个序列返回错误（如 4xx、解析失败）。"""

    def __init__(self, fail: set[str] = frozenset(), bad: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.fail, self.bad = fail, bad
        self.calls: list[str] = []

    def observations(self, series: str, start: date) -> list[Obs]:
        self.calls.append(series)
        if series in self.fail:
            raise FredUnreachable("down")
        if series in self.bad:
            raise DataUnavailableError("FRED X 返回 404")
        if series == "PAYEMS":
            return [Obs(date(2026, 9, 1), 159_000.0), Obs(date(2026, 10, 1), 159_063.0)]
        return [Obs(date(2026, 11, 4), 17.0), Obs(date(2026, 11, 5), 17.5)]

    def last_updated(self, series: str) -> datetime:
        return datetime(2026, 11, 6, 13, 40, tzinfo=UTC)


class FakeFinnhub:
    def __init__(self, auth: bool = False) -> None:
        self.auth = auth
        self.company: list[str] = []
        self.earn_days: list[date] = []

    def _check(self) -> None:
        if self.auth:
            raise FinnhubAuthError("bad key")

    def market_news(self) -> list[NewsRaw]:
        self._check()
        return [NewsRaw("m1", T_NEWS, "Fed holds rates", "Reuters", "https://n/m1", ())]

    def company_news(self, symbol: str, start: date, end: date) -> list[NewsRaw]:
        self._check()
        self.company.append(symbol)
        return [NewsRaw(f"c-{symbol}", T_NEWS, f"{symbol} jumps on deal", "CNBC",
                        f"https://n/{symbol}", (symbol,))]  # fmt: skip

    def earnings(self, day: date) -> list[EarningsRaw]:
        self._check()
        self.earn_days.append(day)
        if day == DAY:
            return [EarningsRaw("S0", DAY, "bmo", 1.0, 1.2)]
        return []


FF_ROWS = [{"title": "Non-Farm Employment Change", "country": "USD",
            "date": "2026-11-06T08:30:00-05:00", "impact": "High", "forecast": "63K",
            "previous": "29K"},
           {"title": "ISM Services PMI", "country": "USD", "date": "2026-11-09T10:00:00-05:00",
            "impact": "Medium", "forecast": "54.9", "previous": "54.9"}]  # fmt: skip
BTC_ROWS = [[int(datetime(2026, 11, d, tzinfo=UTC).timestamp()), 1, 2, 1, 70000.0 + d, 5]
            for d in range(1, 7)]  # fmt: skip


def make_fetch(ff: Any = FF_ROWS, btc: Any = BTC_ROWS) -> Any:
    def fetch(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        r = ff if url == FF_URL else btc if url == COINBASE_URL else None
        assert r is not None, url
        return r if isinstance(r, tuple) else (200, r)

    return fetch


def write_snapshot(data_dir: Path, n: int = 3) -> None:
    movers = [{"symbol": f"S{i}", "name": f"Co{i} Inc.", "change_pct": 0.1 - i * 0.001}
              for i in range(n)]  # fmt: skip
    p = ms.snapshot_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"as_of": DAY.isoformat(), "movers": movers}), encoding="utf-8")


def run(data_dir: Path, *, fred: Any = None, finnhub: Any = None, holdings: set[str] | None = None,
        fetch: Any = None) -> str:  # fmt: skip
    return run_market_extras(data_dir=data_dir, now=NOW, today=DAY, cal=CAL,
                             holdings=holdings if holdings is not None else {"S1", "HOLD"},
                             fred=fred, finnhub=finnhub, fetch=fetch or make_fetch())  # fmt: skip


def read(data_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((ms.market_dir(data_dir) / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_extras_writes_three_files(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    summary = run(tmp_path, fred=FakeFred(), finnhub=FakeFinnhub())
    macro = read(tmp_path, "macro.json")
    assert [i["symbol"] for i in macro["items"]] == ["VIX", "US10Y", "USD", "WTI", "BTC"]
    news = read(tmp_path, "news.json")
    assert news["as_of"] == DAY.isoformat()
    cats = {i["headline"]: i["category"] for i in news["items"]}
    assert cats["Fed holds rates"] == "macro" and cats["HOLD jumps on deal"] == "company"
    assert "S0 jumps on deal" not in cats  # 非持仓只用于原因
    assert news["reasons"]["S0"]["headline"] == "S0 jumps on deal"
    cal = read(tmp_path, "calendar.json")
    rel = {i["title"]: i for i in cal["released"]}
    assert rel["Non-Farm Employment Change"]["actual"] == "63K"
    assert cal["upcoming"][0]["title"] == "ISM Services PMI"
    assert "宏观 5 项" in summary and "新闻" in summary and "日历" in summary


def test_extras_one_source_failing_keeps_others(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    run(tmp_path, fred=FakeFred(), finnhub=FakeFinnhub())
    summary = run(tmp_path, fred=FakeFred(), finnhub=FakeFinnhub(),
                  fetch=make_fetch(ff=(200, "<html>"), btc=(503, "down")))  # fmt: skip
    macro = read(tmp_path, "macro.json")["items"]
    assert [i["symbol"] for i in macro] == ["VIX", "US10Y", "USD", "WTI", "BTC"]  # BTC 沿用上次
    assert macro[-1]["close"] == 70006.0
    titles = [i["title"] for i in read(tmp_path, "calendar.json")["released"]]
    assert "Non-Farm Employment Change" in titles  # 沿用累积的 FF 事件
    assert "Forex Factory" in summary and "Coinbase" in summary


def test_extras_auth_error_raises_after_others_written(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    with pytest.raises(FinnhubAuthError):
        run(tmp_path, fred=FakeFred(), finnhub=FakeFinnhub(auth=True))
    assert read(tmp_path, "macro.json")["items"]
    assert read(tmp_path, "calendar.json")["released"]


def test_news_without_holdings_or_snapshot(tmp_path: Path) -> None:
    run(tmp_path, fred=FakeFred(), finnhub=FakeFinnhub(), holdings=set())
    news = read(tmp_path, "news.json")
    assert {i["category"] for i in news["items"]} == {"macro"}
    assert news["reasons"] == {} and news["as_of"] is None


def test_extras_without_keys_skips_fred_and_finnhub(tmp_path: Path) -> None:
    summary = run(tmp_path)
    assert [i["symbol"] for i in read(tmp_path, "macro.json")["items"]] == ["BTC"]
    assert not (ms.market_dir(tmp_path) / "news.json").exists()
    cal = read(tmp_path, "calendar.json")
    kinds = {i["kind"] for i in cal["released"] + cal["upcoming"]}
    assert kinds <= {"econ", "fomc"} and "econ" in kinds
    assert all(i["actual"] is None for i in cal["released"])
    assert "未配置" in summary


def test_finnhub_requests_bounded(tmp_path: Path) -> None:
    write_snapshot(tmp_path, n=50)
    holdings = {f"S{i}" for i in range(20, 30)} | {f"H{i}" for i in range(10)}
    fh = FakeFinnhub()
    run(tmp_path, fred=FakeFred(), finnhub=fh, holdings=holdings)
    assert len(fh.company) == 30 + 10 and len(set(fh.company)) == 40
    assert len(fh.earn_days) <= 9
    assert fh.earn_days[:2] == [CAL.previous_trading_day(DAY), DAY]
    assert fh.earn_days[-1] == DAY + timedelta(days=7)


def test_scheduler_includes_market_extras() -> None:
    from quant.core.settings import ScheduleSettings
    from quant.engine.executor import ET
    from quant.jobs.daily import JOB_FUNCS, JOBS
    from quant.jobs.scheduler import day_plan

    plan = dict(day_plan(date(2026, 10, 30), ScheduleSettings(), CAL))
    assert plan["market_extras"] == datetime(2026, 10, 30, 21, 0, tzinfo=ET)
    assert "market_extras" in JOBS and "market_extras" in JOB_FUNCS


def test_job_empty_keys_treated_as_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    import quant.market.extras_job as ej
    from quant.core.settings import Settings
    from quant.jobs.daily import market_extras

    seen: dict[str, Any] = {}

    def fake(**kw: Any) -> str:
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(ej, "run_market_extras", fake)

    class Ctx:
        settings = Settings(broker="sim", TRADING_MODE="sim", data_dir=tmp_path).model_copy(
            update={"fred_api_key": SecretStr(""), "finnhub_api_key": SecretStr(" ")}
        )
        engine = None
        calendar = CAL

        @staticmethod
        def now() -> datetime:
            return NOW

    assert market_extras(Ctx(), DAY) == "ok"  # type: ignore[arg-type]
    assert seen["fred"] is None and seen["finnhub"] is None and seen["today"] == DAY


def test_macro_failure_keeps_previous_items(tmp_path: Path) -> None:
    run(tmp_path, fred=FakeFred(), finnhub=None)
    run(tmp_path, fred=FakeFred(fail={"VIXCLS", "DGS10"}), finnhub=None)
    items = read(tmp_path, "macro.json")["items"]
    assert [i["symbol"] for i in items] == ["VIX", "US10Y", "USD", "WTI", "BTC"]


def test_fred_auth_error_raises_after_others_written(tmp_path: Path) -> None:
    from quant.data.providers.fred import FredAuthError

    class BadFred(FakeFred):
        def observations(self, series: str, start: date) -> list[Obs]:
            raise FredAuthError("bad key")

    write_snapshot(tmp_path)
    with pytest.raises(FredAuthError):
        run(tmp_path, fred=BadFred(), finnhub=FakeFinnhub())
    assert read(tmp_path, "news.json")["items"]
    assert [i["symbol"] for i in read(tmp_path, "macro.json")["items"]] == ["BTC"]
    assert read(tmp_path, "calendar.json")["released"]


def test_reasons_include_holdings_outside_top(tmp_path: Path) -> None:
    write_snapshot(tmp_path, n=40)  # S35 不在前 30，但是持仓
    run(tmp_path, fred=None, finnhub=FakeFinnhub(), holdings={"S35"})
    assert "S35" in read(tmp_path, "news.json")["reasons"]


def test_company_news_end_is_next_day(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    ends: list[date] = []

    class F(FakeFinnhub):
        def company_news(self, symbol: str, start: date, end: date) -> list[NewsRaw]:
            ends.append(end)
            return super().company_news(symbol, start, end)

    run(tmp_path, fred=None, finnhub=F())
    assert ends and set(ends) == {DAY + timedelta(days=1)}


def test_fred_stops_after_network_failure(tmp_path: Path) -> None:
    fred = FakeFred(fail={"VIXCLS"})
    summary = run(tmp_path, fred=fred, finnhub=None)
    assert fred.calls == ["VIXCLS"]  # 宏观与实际值都不再请求
    assert summary.count("FRED") == 1


def test_fred_single_series_error_does_not_stop_others(tmp_path: Path) -> None:
    fred = FakeFred(bad={"VIXCLS"})
    summary = run(tmp_path, fred=fred, finnhub=None)
    assert fred.calls[0] == "VIXCLS" and len(fred.calls) > 1  # 其它序列照常请求
    assert "VIXCLS" in summary
    symbols = [i["symbol"] for i in read(tmp_path, "macro.json")["items"]]
    assert "VIX" not in symbols and "US10Y" in symbols


def test_failed_refresh_preserves_previous_news_and_calendar_timestamps(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    run(tmp_path, fred=FakeFred(), finnhub=FakeFinnhub())
    news_before = (ms.news_path(tmp_path)).read_text()
    calendar_before = ms.calendar_path(tmp_path).read_text()
    with pytest.raises(FinnhubAuthError):
        run_market_extras(
            data_dir=tmp_path,
            now=NOW + timedelta(minutes=20),
            today=DAY,
            cal=CAL,
            holdings={"S1"},
            fred=FakeFred(fail={"VIXCLS"}),
            finnhub=FakeFinnhub(auth=True),
            fetch=make_fetch(),
        )
    assert ms.news_path(tmp_path).read_text() == news_before
    assert ms.calendar_path(tmp_path).read_text() == calendar_before


def test_intraday_news_reasons_follow_todays_movers(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    p = ms.snapshot_path(tmp_path)
    old = json.loads(p.read_text())
    old["as_of"] = CAL.previous_trading_day(DAY).isoformat()
    p.write_text(json.dumps(old))
    intra = {
        "intraday": True,
        "as_of": DAY.isoformat(),
        "movers": [
            {"symbol": "FRESH", "name": "Fresh Inc.", "change_pct": 0.15},
        ],
    }
    ms.intraday_snapshot_path(tmp_path).write_text(json.dumps(intra))
    run(tmp_path, finnhub=FakeFinnhub(), fred=FakeFred(), holdings=set())
    news = read(tmp_path, "news.json")
    assert news["as_of"] == DAY.isoformat() and "FRESH" in news["reasons"]
    assert "S0" not in news["reasons"]
