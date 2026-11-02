"""美股交易时段（#93）：盘前 / 常规 / 盘后 / 夜盘 / 休市，按 XNYS（含半日市）。

- 盘前 04:00–开盘；常规 开盘–收盘；盘后 收盘–收盘后 4 小时（半日市为 13:00–17:00）。
- 夜盘：盘后结束（非交易日为 20:00）到次日 04:00，且次日为交易日
  （周日晚有；周五晚与节假日前一晚没有）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from quant.core.calendar import TradingCalendar

ET = ZoneInfo("America/New_York")
Session = Literal["pre", "regular", "post", "overnight", "closed"]
SESSION_TEXT: dict[str, str] = {
    "pre": "盘前",
    "regular": "盘中",
    "post": "盘后",
    "overnight": "夜盘",
    "closed": "休市",
}
PRE_OPEN = time(4, 0)
POST_HOURS = timedelta(hours=4)


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, ET)


def us_session(cal: TradingCalendar, now: datetime) -> Session:
    et = now.astimezone(ET)
    d = et.date()
    if cal.is_trading_day(d):
        if et < _at(d, PRE_OPEN):
            return "overnight"  # 前一晚 20:00 起的夜盘延续到 04:00
        open_, close = cal.session_open(d), cal.session_close(d)
        if et < open_:
            return "pre"
        if et < close:
            return "regular"
        if et < close + POST_HOURS:
            return "post"
        night_start = close + POST_HOURS
    else:
        night_start = _at(d, time(20, 0))
    if et >= night_start and cal.is_trading_day(d + timedelta(days=1)):
        return "overnight"
    return "closed"
