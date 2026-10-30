"""事件数据：FOMC 决议日（swing_pullback Task 2）；实盘财报日（#47）。"""

from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path

from quant.data.events import (
    live_earnings_path,
    load_earnings,
    load_events,
    load_fomc_dates,
    save_earnings,
    sync_live_earnings,
)


def test_fomc_known_dates() -> None:
    days = load_fomc_dates()
    for d in (date(2022, 6, 15), date(2024, 9, 18), date(2025, 12, 10), date(2026, 10, 28)):
        assert d in days


def test_fomc_range_sorted() -> None:
    days = load_fomc_dates()
    assert days[0] <= date(2014, 1, 29) and days[-1] >= date(2027, 1, 27)
    assert all(a < b for a, b in pairwise(days))
    counts = Counter(d.year for d in days)
    # 每年 8 次例会；2020 年 3 月 17–18 日例会取消（改为 3 月 15 日临时会议，不计入）
    assert {y: counts[y] for y in range(2014, 2028)} == {
        y: (7 if y == 2020 else 8) for y in range(2014, 2028)
    }


def test_fomc_excludes_unscheduled() -> None:
    days = load_fomc_dates()
    assert date(2020, 3, 15) not in days and date(2019, 10, 4) not in days


# ---- 实盘财报日（第二份计划任务 4） ----


def test_sync_live_earnings_merges(tmp_path: Path) -> None:
    path = live_earnings_path(tmp_path)
    assert path == tmp_path / "events" / "earnings_live.csv"
    calls: list[tuple[date, date]] = []

    def first(start: date, end: date) -> dict[str, list[date]]:
        calls.append((start, end))
        return {"AAPL": [date(2025, 10, 31), date(2026, 1, 30)], "MSFT": [date(2026, 1, 29)]}

    sync_live_earnings(path, first, date(2026, 2, 2), back_days=400, ahead_days=45)
    assert calls == [(date(2024, 12, 29), date(2026, 3, 19))]

    def second(start: date, end: date) -> dict[str, list[date]]:
        # 一年后：区间已不含 2025-10-31；1-29 的 MSFT 改期到 1-28
        return {"AAPL": [date(2026, 11, 3)], "MSFT": [date(2026, 1, 28)]}

    got = sync_live_earnings(path, second, date(2026, 12, 1), back_days=320, ahead_days=45)
    assert got["AAPL"] == (date(2025, 10, 31), date(2026, 11, 3))  # 区间外旧日期保留
    assert got["MSFT"] == (date(2026, 1, 28),)  # 区间内以最新结果为准
    assert load_earnings(path)["MSFT"] == (date(2026, 1, 28),)


def test_load_events(tmp_path: Path) -> None:
    ev = load_events(tmp_path)  # 两个文件都不存在
    assert ev.earnings == {} and ev.is_fomc(date(2024, 12, 18))
    save_earnings(tmp_path / "events" / "earnings.csv", {"AAPL": [date(2024, 5, 3)]})
    save_earnings(
        live_earnings_path(tmp_path), {"AAPL": [date(2026, 10, 31)], "MU": [date(2026, 9, 24)]}
    )
    ev = load_events(tmp_path)
    assert ev.earnings["AAPL"] == (date(2024, 5, 3), date(2026, 10, 31))
    assert ev.earnings["MU"] == (date(2026, 9, 24),)


def test_sync_keeps_old_dates_when_symbol_missing_from_fetch(tmp_path: Path) -> None:
    path = live_earnings_path(tmp_path)
    first = {"MSFT": [date(2026, 10, 29)], "AAPL": [date(2026, 10, 31)]}
    sync_live_earnings(path, lambda a, b: first, date(2026, 10, 1))
    # 下一次接口漏返了 MSFT：保留其区间内的旧日期，而不是清空
    got = sync_live_earnings(path, lambda a, b: {"AAPL": [date(2026, 10, 31)]}, date(2026, 10, 2))
    assert got["MSFT"] == (date(2026, 10, 29),)
