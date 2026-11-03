"""盘中行情（#93）：读 quotes 服务写入的 intraday_quotes，只读。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant.api.schemas import IntradayOut, IntradayPositionOut, IntradayQuoteOut
from quant.core.calendar import TradingCalendar
from quant.db.models import Fill, IntradayQuote, JobRun, PositionSnapshot
from quant.market.session import ET, SESSION_TEXT, us_session

STALE = timedelta(minutes=20)  # 全市场一轮约 12 分钟，额外预留新闻与日历请求
FIRST_ROUND = timedelta(minutes=15)
VIEW_ONLY = "Finnhub 盘中报价 · 仅供查看，交易用收盘日线"
EXTENDED = "扩展时段行情未接入，盘中估算已暂停；账户数据见快照日期"
NO_DB = "未配置数据库，盘中行情不可用"


def _now() -> datetime:
    return datetime.now(UTC)


def _positions(
    s: Session,
    mode: str,
    quotes: dict[str, IntradayQuote],
    since: datetime,
    now: datetime,
    cal: TradingCalendar,
    out: IntradayOut,
) -> None:
    """盘中费前估算；包含完全卖出部分。当天快照须经过对账，旧快照只接受上一交易日。

    当日盈亏 = 现持股 × 现价 − 隔夜股数 × 昨收 − 今日买入金额 + 今日卖出金额。
    """
    snap_day = s.scalar(
        select(func.max(PositionSnapshot.snapshot_date)).where(PositionSnapshot.mode == mode)
    )
    out.snapshot_date = snap_day
    if snap_day is None:
        out.note += "；暂无持仓基础，无法估算（空仓账户以账户快照为准）"
        return
    today = now.astimezone(ET).date()
    if snap_day not in (today, cal.previous_trading_day(today)):
        out.note += "；持仓快照过旧，无法估算"
        return
    jobs = {
        r.job_name: r.status
        for r in s.scalars(
            select(JobRun).where(
                JobRun.mode == mode,
                JobRun.trade_date == today,
                JobRun.job_name.in_(("daily_execute", "daily_report")),
            )
        )
    }
    if any(status in ("running", "failed") for status in jobs.values()) or (
        snap_day == today and jobs.get("daily_report") != "success"
    ):
        out.note += "；交易或持仓对账尚未完成，暂不估算"
        return
    held = {
        p.symbol: p.qty
        for p in s.scalars(
            select(PositionSnapshot).where(
                PositionSnapshot.mode == mode,
                PositionSnapshot.snapshot_date == snap_day,
            )
        )
    }
    traded: dict[str, list[tuple[int, float]]] = {}
    for f in s.scalars(
        select(Fill).where(
            Fill.mode == mode,
            Fill.filled_at >= since,
            Fill.filled_at <= now,
        )
    ):
        sign = 1 if f.side.upper() == "BUY" else -1
        traded.setdefault(f.symbol, []).append((sign * f.qty, float(f.price)))
    for sym in sorted(held.keys() | traded.keys()):
        fills = traded.get(sym, [])
        delta = sum(n for n, _ in fills)
        qty = held.get(sym, 0) if snap_day == today else held.get(sym, 0) + delta
        carried = qty - delta
        if qty < 0 or carried < 0:
            out.note += "；持仓与成交不一致，无法估算"
            out.positions = []
            return
        if qty == 0 and not fills:
            continue
        q = quotes.get(sym)
        stale = q is not None and now - q.quote_time > STALE
        if q is None:
            out.missing_symbols.append(sym)
        elif stale:
            out.stale_symbols.append(sym)
        pnl = None
        if q is not None and not stale and (q.prev_close is not None or carried == 0):
            base = carried * (q.prev_close or 0.0) + sum(n * px for n, px in fills)
            pnl = qty * q.price - base
        out.positions.append(
            IntradayPositionOut(
                symbol=sym,
                qty=qty,
                price=q.price if q else None,
                quote_time=q.quote_time if q else None,
                stale=stale,
                change_pct=q.change_pct if q else None,
                day_pnl=pnl,
                traded_today=bool(fills),
            )
        )
    out.complete = bool(out.positions) and all(p.day_pnl is not None for p in out.positions)
    if out.complete:
        out.day_pnl = sum(p.day_pnl or 0.0 for p in out.positions)
        out.market_value = sum(p.qty * (p.price or 0.0) for p in out.positions)
    elif out.positions:
        out.note += "；行情缺失或延迟，暂不汇总"


def intraday(s: Session | None, cal: TradingCalendar, mode: str | None = None) -> IntradayOut:
    now = _now()
    session = us_session(cal, now)
    out = IntradayOut(session=session, session_text=SESSION_TEXT[session])
    if session != "regular":
        out.note = (
            "休市，盘中估算已暂停；账户数据见快照日期"
            if session == "closed"
            else f"{out.session_text}：{EXTENDED}"
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
        _positions(s, mode, {r.symbol: r for r in rows}, opened, now, cal, out)
    return out
