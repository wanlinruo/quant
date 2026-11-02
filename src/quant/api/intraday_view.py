"""盘中行情（#93）：读 quotes 服务写入的 intraday_quotes，只读。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant.api.schemas import IntradayOut, IntradayPositionOut, IntradayQuoteOut
from quant.core.calendar import TradingCalendar
from quant.db.models import Fill, IntradayQuote, PositionSnapshot
from quant.market.session import ET, SESSION_TEXT, us_session

STALE = timedelta(minutes=5)
FIRST_ROUND = timedelta(minutes=3)
VIEW_ONLY = "Finnhub 盘中报价 · 仅供查看，交易用收盘日线"
EXTENDED = "扩展时段行情未接入，显示上一交易日收盘"
NO_DB = "未配置数据库，盘中行情不可用"


def _now() -> datetime:
    return datetime.now(UTC)


def _positions(
    s: Session, mode: str, quotes: dict[str, IntradayQuote], since: datetime
) -> list[IntradayPositionOut]:
    """当日盈亏 = 现持股 × 现价 − 隔夜股数 × 昨收 − 今日买入金额 + 今日卖出金额。

    隔夜股数 = 现持股 − 今日买入股数 + 今日卖出股数（调仓日新买入的从成交价算起）。
    """
    snap_day = s.scalar(
        select(func.max(PositionSnapshot.snapshot_date)).where(PositionSnapshot.mode == mode)
    )
    if snap_day is None:
        return []
    held = s.scalars(
        select(PositionSnapshot).where(
            PositionSnapshot.mode == mode, PositionSnapshot.snapshot_date == snap_day
        )
    ).all()
    traded: dict[str, list[tuple[int, float]]] = {}  # 买为正股数
    for f in s.scalars(select(Fill).where(Fill.mode == mode, Fill.filled_at >= since)):
        sign = 1 if f.side.upper() == "BUY" else -1
        traded.setdefault(f.symbol, []).append((sign * f.qty, float(f.price)))
    out = []
    for p in sorted(held, key=lambda x: x.symbol):
        q = quotes.get(p.symbol)
        if q is None or p.qty == 0:
            continue
        fills = traded.get(p.symbol, [])
        carried = p.qty - sum(n for n, _ in fills)
        pnl = None
        if q.prev_close is not None or carried == 0:
            base = carried * (q.prev_close or 0.0) + sum(n * px for n, px in fills)
            pnl = p.qty * q.price - base
        out.append(
            IntradayPositionOut(
                symbol=p.symbol,
                qty=p.qty,
                price=q.price,
                change_pct=q.change_pct,
                day_pnl=pnl,
                traded_today=bool(fills),
            )
        )
    return out


def intraday(s: Session | None, cal: TradingCalendar, mode: str | None = None) -> IntradayOut:
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
    if mode is not None:
        out.positions = _positions(s, mode, {r.symbol: r for r in rows}, opened)
        pnls = [p.day_pnl for p in out.positions if p.day_pnl is not None]
        out.day_pnl = sum(pnls) if pnls else None
    return out
