"""事件数据：FOMC 决议日（swing_pullback Task 2）。"""

from collections import Counter
from datetime import date
from itertools import pairwise

from quant.data.events import load_fomc_dates


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
