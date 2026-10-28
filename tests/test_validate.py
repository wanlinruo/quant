from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar, get_calendar
from quant.data.validate import (
    check_jumps,
    check_prices,
    compare_sources,
    validate_bars,
)

START, END = date(2026, 11, 2), date(2026, 11, 30)  # 含感恩节 11-26 与半日市 11-27


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return get_calendar()


def good_bars(cal: TradingCalendar, start: date = START, end: date = END) -> pd.DataFrame:
    days = pd.DatetimeIndex(cal.trading_days(start, end), name="date")
    n = len(days)
    close = 100 + np.arange(n) * 0.5
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "adj_close": close,
            "volume": np.full(n, 1000, dtype="int64"),
        },
        index=days,
    )


def kinds(report_issues: list) -> list[str]:  # type: ignore[type-arg]
    return [i.kind for i in report_issues]


def test_clean_data_passes(cal: TradingCalendar) -> None:
    r = validate_bars("SPY", good_bars(cal), cal, START, END)
    assert r.ok, r.summary()
    assert r.summary() == "校验通过"


def test_missing_trading_day(cal: TradingCalendar) -> None:
    df = good_bars(cal).drop(pd.Timestamp("2026-11-27"))  # 半日市也是交易日
    r = validate_bars("SPY", df, cal, START, END)
    assert [(i.kind, i.day) for i in r.issues] == [("missing_day", date(2026, 11, 27))]


def test_extra_non_trading_day(cal: TradingCalendar) -> None:
    df = good_bars(cal)
    df.loc[pd.Timestamp("2026-11-26")] = df.iloc[0]  # 感恩节
    df = df.sort_index()
    r = validate_bars("SPY", df, cal, START, END)
    assert ("extra_day", date(2026, 11, 26)) in [(i.kind, i.day) for i in r.issues]


@pytest.mark.parametrize(
    ("col", "value"), [("close", np.nan), ("open", 0.0), ("low", -1.0), ("adj_close", np.nan)]
)
def test_bad_prices(cal: TradingCalendar, col: str, value: float) -> None:
    df = good_bars(cal)
    df.loc[pd.Timestamp("2026-11-10"), col] = value
    issues = check_prices("SPY", df)
    assert [(i.kind, i.day) for i in issues] == [("bad_price", date(2026, 11, 10))]


def test_missing_adj_close_column_values_allowed(cal: TradingCalendar) -> None:
    df = good_bars(cal)
    df["adj_close"] = np.nan  # 老虎数据源不提供
    assert check_prices("SPY", df) == []


def test_ohlc_inconsistent(cal: TradingCalendar) -> None:
    df = good_bars(cal)
    df.loc[pd.Timestamp("2026-11-10"), "high"] = 50.0  # 低于收盘价
    assert kinds(check_prices("SPY", df)) == ["ohlc"]


def test_jump_detected_and_acknowledged(cal: TradingCalendar) -> None:
    df = good_bars(cal)
    df.loc[pd.Timestamp("2026-11-10") :, ["open", "high", "low", "close", "adj_close"]] *= 1.2
    issues = check_jumps("SPY", df)
    assert [(i.kind, i.day) for i in issues] == [("jump", date(2026, 11, 10))]
    assert "+" in issues[0].detail
    assert check_jumps("SPY", df, acknowledged={date(2026, 11, 10)}) == []
    assert check_jumps("SPY", df, max_move=0.25) == []


def test_jump_uses_adj_close_so_dividends_do_not_trigger(cal: TradingCalendar) -> None:
    df = good_bars(cal)
    # 仅拆股调整的 close 在除权日跳水，但含分红复权价连续
    df.loc[pd.Timestamp("2026-11-10") :, "close"] *= 0.8
    df.loc[pd.Timestamp("2026-11-10") :, ["open", "low"]] *= 0.8
    assert check_jumps("SPY", df) == []


def test_stale_data(cal: TradingCalendar) -> None:
    df = good_bars(cal, end=date(2026, 11, 25))
    r = validate_bars("SPY", df, cal, START, END)
    assert "stale" in kinds(r.issues)
    assert "missing_day" in kinds(r.issues)


def test_end_on_weekend_not_stale(cal: TradingCalendar) -> None:
    df = good_bars(cal, end=date(2026, 11, 27))
    assert validate_bars("SPY", df, cal, START, date(2026, 11, 29)).ok  # 11-29 周日


def test_empty_window(cal: TradingCalendar) -> None:
    r = validate_bars("SPY", good_bars(cal), cal, date(2027, 1, 4), date(2027, 1, 8))
    assert kinds(r.issues) == ["empty"]


def test_compare_sources(cal: TradingCalendar) -> None:
    a = good_bars(cal)
    b = a.copy()
    assert compare_sources("SPY", a, b) == []
    b.loc[pd.Timestamp("2026-11-30"), "close"] = float(a["close"].iloc[-1]) * 1.002
    issues = compare_sources("SPY", a, b)
    assert [(i.kind, i.day) for i in issues] == [("source_diff", date(2026, 11, 30))]
    # 只抽样最近的日期：更早的差异不在样本内
    c = a.copy()
    c.loc[pd.Timestamp("2026-11-02"), "close"] = float(a["close"].iloc[0]) * 1.01
    assert compare_sources("SPY", a, c, sample_days=5) == []
    assert kinds(compare_sources("SPY", a, a.iloc[0:0])) == ["source_diff"]


def test_summary_truncates(cal: TradingCalendar) -> None:
    df = good_bars(cal)
    df[["close"]] = np.nan
    r = validate_bars("SPY", df, cal, START, END)
    assert not r.ok
    assert "共" in r.summary(limit=3)
