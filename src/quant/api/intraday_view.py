"""盘中行情（#93）：读 quotes 服务写入的 intraday_quotes，只读。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api.schemas import IntradayOut, IntradayQuoteOut
from quant.core.calendar import TradingCalendar
from quant.db.models import IntradayQuote
from quant.market.session import ET, SESSION_TEXT, us_session

STALE = timedelta(minutes=5)
FIRST_ROUND = timedelta(minutes=3)
VIEW_ONLY = "Finnhub 盘中报价 · 仅供查看，交易用收盘日线"
EXTENDED = "扩展时段行情未接入，显示上一交易日收盘"
NO_DB = "未配置数据库，盘中行情不可用"


def _now() -> datetime:
    return datetime.now(UTC)


def intraday(s: Session | None, cal: TradingCalendar) -> IntradayOut:
    now = _now()
    session = us_session(cal, now)
    out = IntradayOut(session=session, session_text=SESSION_TEXT[session])
    if session != "regular":
        out.note = (
            "休市，显示上一交易日收盘" if session == "closed" else f"{out.session_text}：{EXTENDED}"
        )
        return out
    if s is None:
        out.note = NO_DB
        return out
    opened = cal.session_open(now.astimezone(ET).date())  # 只取今天开盘后的报价
    rows = s.scalars(
        select(IntradayQuote)
        # 报价时间也要在开盘后：开盘后首轮可能取到昨天收盘的报价（或停牌股的旧价）
        .where(IntradayQuote.fetched_at >= opened, IntradayQuote.quote_time >= opened)
        .order_by(IntradayQuote.symbol)
    ).all()
    out.quotes = [
        IntradayQuoteOut(
            symbol=r.symbol,
            price=r.price,
            prev_close=r.prev_close,
            change_pct=r.change_pct,
            quote_time=r.quote_time,
        )
        for r in rows
    ]
    if rows:
        out.as_of = max(r.quote_time for r in rows)
        fetched = max(r.fetched_at for r in rows)
        out.stale = now - fetched > STALE
    elif now - opened < FIRST_ROUND:
        out.note = "开盘后首轮采集中"  # 不误报延迟
        return out
    else:
        out.stale = True
    out.note = VIEW_ONLY + ("；行情延迟，采集可能中断" if out.stale else "")
    return out
