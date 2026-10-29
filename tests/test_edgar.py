"""财报反应日（SEC EDGAR 8-K Item 2.02，swing_pullback Task 3）。夹具写在测试内，不访问网络。

时区依据（2026-10-29 实测 AAPL submissions）：acceptanceDateTime 为真实 UTC。
AAPL 2024-05-02 盘后 16:30 EDT 发布财报，记录为 "2024-05-02T20:30:34.000Z"；
2024-02-01 16:30 EST 发布，记录为 "2024-02-01T21:30:30.000Z"。
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from quant.core.calendar import TradingCalendar
from quant.data.events import load_earnings, save_earnings
from quant.data.providers.edgar import (
    CIKS,
    fetch_earnings,
    parse_earnings_filings,
    reaction_day,
)

CAL = TradingCalendar()


def _recent(rows: list[tuple[str, str, str]]) -> dict[str, Any]:
    return {
        "form": [r[0] for r in rows],
        "acceptanceDateTime": [r[1] for r in rows],
        "items": [r[2] for r in rows],
    }


def test_parse_items() -> None:
    sub = {
        "filings": {
            "recent": _recent(
                [
                    ("8-K", "2024-05-02T20:30:34.000Z", "2.02,9.01"),
                    ("8-K", "2024-03-01T21:00:00.000Z", "5.02"),
                    ("10-Q", "2024-05-03T20:00:00.000Z", ""),
                ]
            ),
            "files": [],
        }
    }
    got = parse_earnings_filings(sub)
    assert got == [datetime(2024, 5, 2, 20, 30, 34, tzinfo=UTC)]


def test_reaction_day_after_close() -> None:
    assert reaction_day(datetime(2024, 5, 2, 20, 30, 34, tzinfo=UTC), CAL) == date(2024, 5, 3)
    assert reaction_day(datetime(2024, 2, 1, 21, 30, 30, tzinfo=UTC), CAL) == date(2024, 2, 2)


def test_reaction_day_pre_open_and_weekend() -> None:
    # 07:00 EDT = 11:00 UTC → 当日
    assert reaction_day(datetime(2024, 5, 2, 11, 0, tzinfo=UTC), CAL) == date(2024, 5, 2)
    # 盘中 12:00 EDT → 当日
    assert reaction_day(datetime(2024, 5, 2, 16, 0, tzinfo=UTC), CAL) == date(2024, 5, 2)
    # 周六受理 → 下周一
    assert reaction_day(datetime(2024, 5, 4, 15, 0, tzinfo=UTC), CAL) == date(2024, 5, 6)
    # 半日市 2026-11-27 13:00 收盘；13:30 EST = 18:30 UTC → 下一交易日 11-30
    assert reaction_day(datetime(2026, 11, 27, 18, 30, tzinfo=UTC), CAL) == date(2026, 11, 30)


def test_fetch_follows_history_files() -> None:
    recent = {
        "filings": {
            "recent": _recent(
                [
                    ("8-K", "2024-05-02T20:30:34.000Z", "2.02,9.01"),
                    ("8-K", "2015-10-27T20:30:36.000Z", "2.02,9.01"),
                ]
            ),
            "files": [{"name": "CIK0000320193-submissions-001.json"}],
        }
    }
    older = _recent(
        [
            ("8-K", "2015-10-27T20:30:36.000Z", "2.02,9.01"),  # 与 recent 重复
            ("8-K", "2014-07-22T20:30:00.000Z", "2.02,9.01"),
            ("8-K", "2013-10-28T20:30:00.000Z", "2.02,9.01"),  # 2014 年前，过滤
        ]
    )
    urls: list[str] = []

    def fake(url: str) -> dict[str, Any]:
        urls.append(url)
        return recent if url.endswith("CIK0000320193.json") else older

    got = fetch_earnings("AAPL", "test agent@example.com", fetch=fake, sleep=lambda s: None)
    assert got == [date(2014, 7, 23), date(2015, 10, 28), date(2024, 5, 3)]
    assert urls[1].endswith("CIK0000320193-submissions-001.json")


def test_googl_uses_both_ciks() -> None:
    assert CIKS["GOOGL"] == (1652044, 1288776)
    seen: list[str] = []

    def fake(url: str) -> dict[str, Any]:
        seen.append(url)
        return {"filings": {"recent": _recent([]), "files": []}}

    assert fetch_earnings("GOOGL", "a b@c.d", fetch=fake, sleep=lambda s: None) == []
    assert any("CIK0001652044" in u for u in seen) and any("CIK0001288776" in u for u in seen)


def test_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "events" / "earnings.csv"
    data = {"AAPL": [date(2024, 5, 3), date(2024, 8, 2)], "MSFT": [date(2024, 4, 26)]}
    save_earnings(path, data)
    assert load_earnings(path) == {"AAPL": (date(2024, 5, 3), date(2024, 8, 2)),
                                   "MSFT": (date(2024, 4, 26),)}  # fmt: skip


def test_cli_sync_earnings(tmp_path: Path, monkeypatch: Any) -> None:
    from quant import cli
    from quant.data.providers import edgar

    calls: list[tuple[str, str]] = []

    def fake(symbol: str, user_agent: str) -> list[date]:
        calls.append((symbol, user_agent))
        return [date(2024, 5, 3)]

    monkeypatch.setattr(edgar, "fetch_earnings", fake)
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    rc = cli.main(["events", "sync-earnings", "--user-agent", "quant-research a@b.c"])
    assert rc == 0
    assert [c[0] for c in calls] == sorted(CIKS)
    assert all(c[1] == "quant-research a@b.c" for c in calls)
    assert load_earnings(tmp_path / "events" / "earnings.csv")["AAPL"] == (date(2024, 5, 3),)


def test_retrying_fetch() -> None:
    from quant.data.providers.edgar import retrying

    n = {"calls": 0}

    def flaky(url: str) -> dict[str, Any]:
        n["calls"] += 1
        if n["calls"] < 3:
            raise OSError("SSL EOF")
        return {"ok": True}

    slept: list[float] = []
    assert retrying(flaky, retries=3, sleep=slept.append)("u") == {"ok": True}
    assert slept == [2.0, 4.0]

    def down(url: str) -> dict[str, Any]:
        raise OSError("down")

    try:
        retrying(down, retries=2, sleep=lambda s: None)("u")
    except OSError as e:
        assert "down" in str(e)
    else:
        raise AssertionError("应抛出最后一次的错误")
