"""美股交易时段（#93）：盘前 / 常规 / 盘后 / 夜盘 / 休市，按 XNYS（含半日市）。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quant.core.calendar import get_calendar
from quant.market.session import us_session

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        ((2026, 11, 2, 3, 59), "overnight"),  # 周一凌晨（周日 20:00 起的夜盘）
        ((2026, 11, 2, 4, 0), "pre"),
        ((2026, 11, 2, 9, 29), "pre"),
        ((2026, 11, 2, 9, 30), "regular"),
        ((2026, 11, 2, 15, 59), "regular"),
        ((2026, 11, 2, 16, 0), "post"),
        ((2026, 11, 2, 19, 59), "post"),
        ((2026, 11, 2, 20, 0), "overnight"),  # 周二是交易日
        ((2026, 11, 6, 20, 30), "closed"),  # 周五晚：周六不交易
        ((2026, 11, 7, 12, 0), "closed"),  # 周六
        ((2026, 11, 8, 19, 0), "closed"),  # 周日 20:00 前
        ((2026, 11, 8, 20, 0), "overnight"),  # 周日 20:00 起
        ((2026, 11, 26, 12, 0), "closed"),  # 感恩节休市
        ((2026, 11, 25, 21, 0), "closed"),  # 感恩节前一晚：次日休市，不是夜盘
        ((2026, 11, 27, 12, 59), "regular"),  # 半日市 13:00 收盘
        ((2026, 11, 27, 13, 0), "post"),
        ((2026, 11, 27, 17, 0), "closed"),  # 半日市盘后到 17:00；次日周六
    ],
)
def test_us_session(when: tuple[int, int, int, int, int], expected: str) -> None:
    y, mo, d, h, mi = when
    assert us_session(get_calendar(), datetime(y, mo, d, h, mi, tzinfo=ET)) == expected
