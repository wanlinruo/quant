from datetime import UTC, date, datetime, time

import pytest

from quant.core.calendar import ET, TradingCalendar, get_calendar


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return get_calendar()


def test_covers_project_history(cal: TradingCalendar) -> None:
    assert cal.first_day <= date(1993, 1, 29)  # SPY 上市
    assert cal.is_trading_day(date(1993, 1, 29))


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2026, 10, 28), True),  # 周三
        (date(2026, 10, 31), False),  # 周六
        (date(2026, 11, 26), False),  # 感恩节
        (date(2026, 11, 27), True),  # 感恩节次日（半日市）
        (date(2026, 12, 25), False),  # 圣诞节
        (date(2026, 1, 19), False),  # 马丁·路德·金纪念日
        (date(2026, 6, 19), False),  # 六月节（2022 年起休市）
        (date(2026, 4, 3), False),  # 耶稣受难日
        (date(2001, 9, 11), False),  # 9·11 临时休市
        (date(2012, 10, 29), False),  # 飓风桑迪
    ],
)
def test_is_trading_day(cal: TradingCalendar, d: date, expected: bool) -> None:
    assert cal.is_trading_day(d) is expected


def test_next_previous_across_holiday(cal: TradingCalendar) -> None:
    assert cal.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)
    assert cal.previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)
    # 非交易日作为起点
    assert cal.next_trading_day(date(2026, 11, 26)) == date(2026, 11, 27)
    assert cal.previous_trading_day(date(2026, 11, 1)) == date(2026, 10, 30)
    assert cal.next_trading_day(date(2026, 10, 30)) == date(2026, 11, 2)


def test_trading_days_range(cal: TradingCalendar) -> None:
    days = cal.trading_days(date(2026, 11, 23), date(2026, 11, 29))
    assert days == [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)]
    assert cal.trading_days(date(2026, 11, 29), date(2026, 11, 23)) == []


def test_regular_session_times(cal: TradingCalendar) -> None:
    o, c = cal.session_open(date(2026, 10, 28)), cal.session_close(date(2026, 10, 28))
    assert o.tzinfo is not None
    assert (o.astimezone(ET).time(), c.astimezone(ET).time()) == (time(9, 30), time(16, 0))
    assert not cal.is_early_close(date(2026, 10, 28))


@pytest.mark.parametrize("d", [date(2026, 11, 27), date(2026, 12, 24), date(2025, 7, 3)])
def test_half_days(cal: TradingCalendar, d: date) -> None:
    assert cal.is_early_close(d)
    assert cal.session_close(d).astimezone(ET).time() == time(13, 0)


def test_dst_switch_keeps_et_times(cal: TradingCalendar) -> None:
    # 2026-11-01 美国结束夏令时：美东时间不变，UTC 时间后移 1 小时
    before, after = date(2026, 10, 30), date(2026, 11, 2)
    assert cal.session_open(before).astimezone(ET).time() == time(9, 30)
    assert cal.session_open(after).astimezone(ET).time() == time(9, 30)
    assert cal.session_open(before).astimezone(UTC).time() == time(13, 30)
    assert cal.session_open(after).astimezone(UTC).time() == time(14, 30)
    # 3 月第二个周日开始夏令时
    assert cal.session_open(date(2026, 3, 6)).astimezone(UTC).time() == time(14, 30)
    assert cal.session_open(date(2026, 3, 9)).astimezone(UTC).time() == time(13, 30)


def test_month_end(cal: TradingCalendar) -> None:
    assert cal.is_month_end(date(2026, 10, 30))  # 10/31 是周六
    assert not cal.is_month_end(date(2026, 10, 29))
    assert not cal.is_month_end(date(2026, 10, 31))  # 非交易日
    ends = cal.month_end_trading_days(date(2026, 1, 1), date(2026, 12, 31))
    assert len(ends) == 12
    assert ends[0] == date(2026, 1, 30)
    assert ends[-1] == date(2026, 12, 31)
    # 区间截在月中时，不包含该月
    assert cal.month_end_trading_days(date(2026, 10, 1), date(2026, 10, 29)) == []


def test_last_completed_trading_day(cal: TradingCalendar) -> None:
    def at(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
        return datetime(y, m, d, hh, mm, tzinfo=ET)

    assert cal.last_completed_trading_day(at(2026, 10, 28, 15, 59)) == date(2026, 10, 27)
    assert cal.last_completed_trading_day(at(2026, 10, 28, 16, 0)) == date(2026, 10, 28)
    assert cal.last_completed_trading_day(at(2026, 11, 1, 12)) == date(2026, 10, 30)  # 周日
    # 半日市 13:00 收盘
    assert cal.last_completed_trading_day(at(2026, 11, 27, 13, 30)) == date(2026, 11, 27)
    # 北京时间 10-29 08:00 = 美东 10-28 20:00
    from zoneinfo import ZoneInfo

    bj = datetime(2026, 10, 29, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert cal.last_completed_trading_day(bj) == date(2026, 10, 28)
    with pytest.raises(ValueError, match="时区"):
        cal.last_completed_trading_day(datetime(2026, 10, 28, 17))


def test_input_validation(cal: TradingCalendar) -> None:
    with pytest.raises(TypeError):
        cal.is_trading_day(datetime(2026, 10, 28, 12))
    with pytest.raises(ValueError, match="范围"):
        cal.is_trading_day(date(1980, 1, 2))
    with pytest.raises(ValueError, match="不是交易日"):
        cal.session_close(date(2026, 11, 26))
