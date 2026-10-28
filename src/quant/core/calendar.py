"""交易日历：基于 exchange_calendars 的 XNYS（纽交所），业务时区 America/New_York。

交易日、休市日、半日市一律以交易所日历为准，不硬编码（PROJECT_PLAN 第 8 章）。
对外只暴露 date 与带时区的 datetime，不暴露 pandas / exchange_calendars 类型。
"""

from __future__ import annotations

from datetime import date, datetime
from functools import cache
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import pandas as pd

ET = ZoneInfo("America/New_York")
# 覆盖本项目用到的全部历史（SPY 1993 年上市）；exchange_calendars 默认只给近 20 年
CALENDAR_START = "1990-01-02"


def _ts(d: date) -> pd.Timestamp:
    if isinstance(d, datetime):
        raise TypeError("请传入 date（交易日），不是 datetime")
    return pd.Timestamp(d)


class TradingCalendar:
    def __init__(self, code: str = "XNYS", start: str = CALENDAR_START) -> None:
        self._cal = xc.get_calendar(code, start=start)
        self.first_day: date = self._cal.first_session.date()
        self.last_day: date = self._cal.last_session.date()

    def _check_range(self, d: date) -> None:
        if not (self.first_day <= d <= self.last_day):
            raise ValueError(f"{d} 超出交易日历范围 {self.first_day} ~ {self.last_day}")

    def is_trading_day(self, d: date) -> bool:
        self._check_range(d)
        return bool(self._cal.is_session(_ts(d)))

    def next_trading_day(self, d: date) -> date:
        """d 之后（不含 d）的第一个交易日。"""
        if self.is_trading_day(d):
            return pd.Timestamp(self._cal.next_session(_ts(d))).date()
        return pd.Timestamp(self._cal.date_to_session(_ts(d), direction="next")).date()

    def previous_trading_day(self, d: date) -> date:
        """d 之前（不含 d）的最后一个交易日。"""
        if self.is_trading_day(d):
            return pd.Timestamp(self._cal.previous_session(_ts(d))).date()
        return pd.Timestamp(self._cal.date_to_session(_ts(d), direction="previous")).date()

    def trading_days(self, start: date, end: date) -> list[date]:
        """[start, end] 内的全部交易日，含两端。"""
        if start > end:
            return []
        self._check_range(start)
        self._check_range(end)
        return [ts.date() for ts in self._cal.sessions_in_range(_ts(start), _ts(end))]

    def session_open(self, d: date) -> datetime:
        return self._session_time(d, "open")

    def session_close(self, d: date) -> datetime:
        """收盘时间（美东）；半日市为 13:00。"""
        return self._session_time(d, "close")

    def _session_time(self, d: date, which: str) -> datetime:
        if not self.is_trading_day(d):
            raise ValueError(f"{d} 不是交易日")
        ts = self._cal.session_open(_ts(d)) if which == "open" else self._cal.session_close(_ts(d))
        return ts.tz_convert(ET).to_pydatetime()  # type: ignore[no-any-return]

    def is_early_close(self, d: date) -> bool:
        close = self.session_close(d)
        return (close.hour, close.minute) < (16, 0)

    def is_month_end(self, d: date) -> bool:
        """是否为当月最后一个交易日（月度调仓日）。"""
        return self.is_trading_day(d) and self.next_trading_day(d).month != d.month

    def month_end_trading_days(self, start: date, end: date) -> list[date]:
        """[start, end] 内每个月的最后一个交易日。"""
        return [d for d in self.trading_days(start, end) if self.is_month_end(d)]

    def last_completed_trading_day(self, now: datetime) -> date:
        """截至 now 已收盘的最近一个交易日（用于判断数据是否最新）。"""
        if now.tzinfo is None:
            raise ValueError("now 必须带时区")
        today = now.astimezone(ET).date()
        if self.is_trading_day(today) and now >= self.session_close(today):
            return today
        return self.previous_trading_day(today)


@cache
def get_calendar() -> TradingCalendar:
    """进程内共享的 XNYS 日历（构造较慢）。"""
    return TradingCalendar()
