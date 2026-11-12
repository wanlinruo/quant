"""财经日历（#69 第 2 批 Task 5）。"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from quant.data.providers.finnhub import EarningsRaw
from quant.data.providers.forexfactory import FfEvent
from quant.data.providers.fred import Obs
from quant.market.calendar import build_calendar, fred_actual, merge_ff

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=ET)


def test_actual_requires_fred_updated_on_event_day() -> None:
    obs = [Obs(date(2026, 9, 1), 159_000.0), Obs(date(2026, 10, 1), 159_063.0)]
    upd_same = datetime(2026, 11, 6, 13, 40, tzinfo=UTC)
    upd_old = datetime(2026, 10, 2, 13, 24, tzinfo=UTC)
    nfp = "Non-Farm Employment Change"
    assert fred_actual(nfp, date(2026, 11, 6), obs, upd_same) == "63K"
    assert fred_actual(nfp, date(2026, 11, 6), obs, upd_old) is None
    assert fred_actual(nfp, date(2026, 11, 6), obs, None) is None
    assert fred_actual("ISM Manufacturing PMI", date(2026, 11, 2), obs, upd_same) is None


def test_actual_formats() -> None:
    upd = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)
    d = date(2026, 11, 13)
    assert fred_actual("Unemployment Rate", d, [Obs(date(2026, 10, 1), 4.2)], upd) == "4.2%"
    cpi = [Obs(date(2026, 9, 1), 320.0), Obs(date(2026, 10, 1), 320.96)]
    assert fred_actual("CPI m/m", d, cpi, upd) == "0.3%"
    assert fred_actual("Unemployment Claims", d, [Obs(date(2026, 11, 8), 197_000.0)], upd) == "197K"
    assert fred_actual("JOLTS Job Openings", d, [Obs(date(2026, 9, 1), 7110.0)], upd) == "7.11M"
    assert fred_actual("CPI m/m", d, cpi[:1], upd) is None  # 环比需要两期


def ev(t: str, d: str, f: str = "1") -> FfEvent:
    return FfEvent(t, datetime.fromisoformat(d).astimezone(ET), 3, f, "0")


def test_merge_ff_accumulates_and_prunes() -> None:
    first = [ev("A", "2026-10-15T08:30:00-04:00"), ev("B", "2026-10-30T08:30:00-04:00", "1")]
    old = merge_ff([], first, date(2026, 10, 30))
    second = [ev("B", "2026-10-30T08:30:00-04:00", "2"), ev("C", "2026-11-02T10:00:00-05:00")]
    new = merge_ff(old, second, date(2026, 11, 2))
    assert [(e["title"], e["forecast"]) for e in new] == [("B", "2"), ("C", "1")]  # A 已超过 14 天
    assert new[0]["time"] == "2026-10-30T08:30:00-04:00" and new[0]["impact"] == 3


def test_merge_ff_keeps_only_high_medium() -> None:
    low = FfEvent("L", datetime(2026, 11, 2, 9, 45, tzinfo=ET), 1, "", "")
    hol = FfEvent("H", datetime(2026, 11, 1, 2, 0, tzinfo=ET), 0, "", "")
    assert merge_ff([], [low, hol, ev("M", "2026-11-02T10:00:00-05:00")], date(2026, 11, 2)) == [
        {"title": "M", "time": "2026-11-02T10:00:00-05:00", "impact": 3, "forecast": "1",
         "previous": "0"}
    ]  # fmt: skip


def test_calendar_split_and_grouping() -> None:
    ff = [{"title": "Non-Farm Employment Change", "time": "2026-11-06T08:30:00-05:00",
           "impact": 3, "forecast": "63K", "previous": "29K"},
          {"title": "ISM Services PMI", "time": "2026-11-09T10:00:00-05:00", "impact": 2,
           "forecast": "54.9", "previous": "54.9"},
          {"title": "Old", "time": "2026-11-04T10:00:00-05:00", "impact": 2, "forecast": "",
           "previous": ""}]  # fmt: skip
    earn = [EarningsRaw("XOM", date(2026, 11, 6), "bmo", 1.88, 1.95),
            EarningsRaw("CVX", date(2026, 11, 6), "bmo", 2.43, 2.5),
            EarningsRaw("AAPL", date(2026, 11, 10), "amc", 1.6, None),
            EarningsRaw("MSFT", date(2026, 11, 10), "amc", 3.1, None),
            EarningsRaw("ZZZ", date(2026, 11, 10), "amc", 0.1, None)]  # fmt: skip
    released, upcoming = build_calendar(
        ff, {"Non-Farm Employment Change|2026-11-06": "63K"}, earn,
        [date(2026, 12, 9), date(2026, 11, 18)], holdings={"CVX"}, big={"XOM", "AAPL", "MSFT"},
        now=NOW, prev_day=date(2026, 11, 5),
    )  # fmt: skip
    r = {i.title: i for i in released}
    assert set(r) == {
        "Non-Farm Employment Change",
        "CVX · XOM 财报",
    }  # “Old” 早于上一交易日；持仓股排前
    assert r["Non-Farm Employment Change"].actual == "63K"
    assert (
        r["Non-Farm Employment Change"].forecast == "63K"
        and r["Non-Farm Employment Change"].kind == "econ"
    )
    eq = r["CVX · XOM 财报"]
    assert eq.held and eq.impact == 3 and eq.session == "盘前" and eq.kind == "earnings"
    assert eq.actual == "2.5" and eq.forecast == "2.43" and eq.symbols == ["CVX", "XOM"]
    assert [i.title for i in released] == [
        "Non-Farm Employment Change",
        "CVX · XOM 财报",
    ]  # 时间降序
    u = [i.title for i in upcoming]
    assert u == ["ISM Services PMI", "AAPL · MSFT 财报", "FOMC 利率决议"]  # 12-09 超过 14 天
    assert upcoming[1].impact == 2 and not upcoming[1].held and upcoming[1].session == "盘后"
    fomc = upcoming[-1]
    assert fomc.time.startswith("2026-11-18T14:00") and fomc.impact == 3 and fomc.kind == "fomc"


def test_calendar_many_earnings_title() -> None:
    syms = ["A1", "A2", "A3", "A4", "A5", "A6"]
    earn = [EarningsRaw(s, date(2026, 11, 9), "", None, None) for s in syms]
    _, up = build_calendar([], {}, earn, [], set(), set(syms), NOW, date(2026, 11, 5))
    assert up[0].title == "A1 · A2 · A3 · A4 等 6 只财报"
    assert up[0].session is None and up[0].date_only


def test_eps_rounded() -> None:
    earn = [EarningsRaw("AAPL", date(2026, 11, 10), "amc", 2.0221, None),
            EarningsRaw("MSFT", date(2026, 11, 11), "amc", 3.8, None),
            EarningsRaw("XOM", date(2026, 11, 12), "bmo", 1.95, None)]  # fmt: skip
    _, up = build_calendar([], {}, earn, [], {"AAPL", "MSFT", "XOM"}, set(), NOW, date(2026, 11, 5))
    assert [i.forecast for i in up] == ["2.02", "3.8", "1.95"]


def test_fomc_not_duplicated_with_ff() -> None:
    ff = [{"title": "Federal Funds Rate", "time": "2026-11-18T14:00:00-05:00", "impact": 3,
           "forecast": "3.75%", "previous": "4.00%"},
          {"title": "FOMC Statement", "time": "2026-11-18T14:00:00-05:00", "impact": 3,
           "forecast": "", "previous": ""}]  # fmt: skip
    _, up = build_calendar(ff, {}, [], [date(2026, 11, 18)], set(), set(), NOW, date(2026, 11, 5))
    titles = [i.title for i in up]
    assert titles.count("FOMC 利率决议") == 1 and "Federal Funds Rate" not in titles
    assert "FOMC Statement" not in titles
    fomc = up[titles.index("FOMC 利率决议")]
    assert fomc.forecast == "3.75%" and fomc.previous == "4.00%" and fomc.impact == 3


def test_fomc_statement_is_kept_and_reconciles_one_day_local_drift() -> None:
    ff = [
        {
            "title": "FOMC Statement",
            "time": "2026-11-18T14:00:00-05:00",
            "impact": 3,
            "forecast": "",
            "previous": "",
        }
    ]
    _, up = build_calendar(ff, {}, [], [date(2026, 11, 17)], set(), set(), NOW, date(2026, 11, 5))
    fomc = [item for item in up if item.kind == "fomc"]
    assert len(fomc) == 1 and fomc[0].time.startswith("2026-11-18T14:00:00")


def test_dmh_is_intraday() -> None:
    earn = [EarningsRaw("AAPL", date(2026, 11, 10), "dmh", 1.0, None)]
    _, up = build_calendar([], {}, earn, [], {"AAPL"}, set(), NOW, date(2026, 11, 5))
    assert up[0].session == "盘中" and up[0].date_only is False
