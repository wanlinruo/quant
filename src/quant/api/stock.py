"""个股页的数据（只读，#65）：本地日线计算的行情摘要与技术面，加上与“我的系统”的关系。

行情不跟随账户；持仓、子仓、排名、成交按账户 mode 读取。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api import analytics as an
from quant.api.portfolio import (
    Stores,
    detail_of,
    held_by_day,
    last_account,
    latest_positions,
    signals_of,
    snapshots_of,
)
from quant.api.schemas import (
    Bar,
    BarsOut,
    EarningsMove,
    EarningsOut,
    FillOut,
    KeyStatsOut,
    Mode,
    PeriodOut,
    QuoteOut,
    RankPoint,
    RelationOut,
    StockProfile,
    SwingCheckOut,
    TechnicalsOut,
)
from quant.core.calendar import TradingCalendar
from quant.data.store import is_valid_symbol
from quant.db.models import Fill as FillRow
from quant.market.periods import period_returns
from quant.strategies.combo import V2_SWING, ComboParams, annual_vol
from quant.strategies.events import EventCalendar
from quant.strategies.swing_pullback import SwingParams
from quant.strategies.universe import Constituents

RANK_HISTORY = 60
EARNINGS_HISTORY = 8
YEAR_DAYS = 365
VOL_RATIO_WINDOW = 20


def bars(stores: Stores, symbol: str, start: date | None) -> BarsOut:
    """主数据目录优先（含分红复权）；没有时用候选池目录（未复权）。不合法或没有数据时为空。"""
    if not is_valid_symbol(symbol):
        return BarsOut(adjusted=True, bars=[])
    df = stores.main.read_bars(symbol, start)
    adjusted = bool(len(df))
    if not adjusted:
        df = stores.universe.read_bars(symbol, start)
    out = [
        Bar(date=d.date(), open=float(o), high=float(h), low=float(lo), close=float(c),
            adj_close=float(a), volume=float(v))
        for d, o, h, lo, c, a, v in zip(
            pd.DatetimeIndex(df.index), df["open"], df["high"], df["low"], df["close"],
            df["adj_close"], df["volume"], strict=True,
        )
    ] if len(df) else []  # fmt: skip
    return BarsOut(adjusted=adjusted or not out, bars=out)


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b - 1 if a is not None and b else None


def _ret_n(px: pd.Series, n: int) -> float | None:
    px = px.dropna()
    return _ratio(float(px.iloc[-1]), float(px.iloc[-1 - n])) if len(px) > n else None


def _quote(df: pd.DataFrame, periods: dict[str, float | None]) -> QuoteOut | None:
    if not len(df):
        return None
    last = df.iloc[-1]
    prev = float(df["close"].iloc[-2]) if len(df) >= 2 else None
    year = df[df.index > df.index[-1] - pd.Timedelta(days=YEAR_DAYS)]
    close = float(last["close"])
    return QuoteOut(
        close=close, change=close - prev if prev is not None else None,
        change_pct=_ratio(close, prev), open=float(last["open"]), high=float(last["high"]),
        low=float(last["low"]), volume=float(last["volume"]),
        turnover=close * float(last["volume"]),
        high_52w=float(year["high"].max()), low_52w=float(year["low"].min()),
        ret_1m=periods.get("1M"), ytd=periods.get("YTD"),
    )  # fmt: skip


def _prices(df: pd.DataFrame) -> pd.Series:
    """计算收益与技术面用的价格：有复权价时用复权价。"""
    if not len(df):
        return pd.Series(dtype=float)
    col = "adj_close" if df["adj_close"].notna().all() else "close"
    return df[col].astype(float)


def profile(
    s: Session,
    mode: Mode,
    symbol: str,
    stores: Stores,
    events: EventCalendar,
    constituents: Constituents | None,
    cal: TradingCalendar,
) -> StockProfile:
    df = stores.bars(symbol)
    adjusted = bool(len(stores.main.read_bars(symbol))) if is_valid_symbol(symbol) else True
    px = _prices(df)
    as_of = pd.Timestamp(df.index[-1]).date() if len(df) else None
    qqq = _prices(stores.main.read_bars("QQQ"))
    spy = _prices(stores.main.read_bars("SPY"))
    periods = period_returns(px, as_of) if as_of else {}
    periods_qqq = period_returns(qqq, as_of) if as_of and len(qqq) else {}

    # 与我的系统的关系（按账户）
    snaps = snapshots_of(s, mode)
    held = latest_positions(snaps)
    order = [r.symbol for r in held]
    acct = last_account(s, mode)
    netliq = float(acct.net_liquidation) if acct else None
    pos = next((r for r in held if r.symbol == symbol), None)
    sigs = signals_of(s, mode, RANK_HISTORY)
    det = detail_of(sigs[0] if sigs else None)
    target = dict(sigs[0].target_weights) if sigs else {}
    i = order.index(symbol) if symbol in order else None
    swing_names = SwingParams.from_mapping(ComboParams().swing_params()).tradables
    relation = RelationOut(
        held=pos is not None,
        weight=float(pos.market_value) / netliq if pos is not None and netliq else None,
        sleeve=det.sleeve_of(symbol, target) if pos is not None or symbol in target else None,
        momentum_rank=det.rank_now(symbol),
        universe_size=int(det.raw["universe_size"])
        if det.raw and det.raw.get("universe_size")
        else None,
        in_sp500=constituents is not None
        and symbol in constituents.members(as_of or max(constituents.snapshots)),
        swing_universe=symbol in swing_names,
        prev_holding=order[i - 1] if i is not None and i > 0 else None,
        next_holding=order[i + 1] if i is not None and i + 1 < len(order) else None,
    )

    # 技术面与关键数据
    ma = {n: an.last(an.sma(px, n)) if len(px) else None for n in (10, 20, 120)}
    c = an.last(px) if len(px) else None
    year = df[df.index > df.index[-1] - pd.Timedelta(days=YEAR_DAYS)] if len(df) else df
    high_52w = float(year["high"].max()) if len(year) else None
    vol = df["volume"].astype(float) if len(df) else pd.Series(dtype=float)
    avg_vol = float(vol.iloc[-VOL_RATIO_WINDOW:].mean()) if len(vol) >= VOL_RATIO_WINDOW else None
    nxt = events.next_earnings(symbol, as_of) if as_of else None
    key_stats = KeyStatsOut(
        volume_ratio=float(vol.iloc[-1]) / avg_vol if avg_vol else None,
        beta_1y=an.risk(px, spy, qqq, window=252).beta_spy if len(px) and len(spy) else None,
        vol_20d=annual_vol(px, 20) if len(px) else None,
        from_high=_ratio(float(df["close"].iloc[-1]), high_52w) if len(df) else None,
        to_ma120=_ratio(c, ma[120]),
        next_earnings=nxt,
        days_to_earnings=len(cal.trading_days(as_of + timedelta(days=1), nxt))
        if as_of and nxt else None,
    )  # fmt: skip
    technicals = TechnicalsOut(
        rsi14=an.last(an.rsi(px, 14)) if len(px) else None,
        atr14_pct=an.atr_pct(df["high"], df["low"], df["close"], 14) if len(df) else None,
        rs_20d=_diff(_ret_n(px, 20), _ret_n(qqq, 20)),
        rs_60d=_diff(_ret_n(px, 60), _ret_n(qqq, 60)),
        ma10=ma[10], ma20=ma[20], ma120=ma[120],
        dist_ma10=_ratio(c, ma[10]), dist_ma20=_ratio(c, ma[20]), dist_ma120=_ratio(c, ma[120]),
    )  # fmt: skip
    slope = int(V2_SWING["slope_days"])
    ma120 = an.sma(px, int(V2_SWING["trend_ma"])).dropna() if len(px) else pd.Series(dtype=float)
    swing = SwingCheckOut(
        trend_up=bool(c > ma120.iloc[-1] and ma120.iloc[-1] > ma120.iloc[-1 - slope])
        if c is not None and len(ma120) > slope else None,
        above_support=bool(c >= ma[20]) if c is not None and ma[20] is not None else None,
        dist_exit=_ratio(c, ma[10]),
    )  # fmt: skip

    # 财报：过去的反应日当天涨跌（相对前一交易日收盘）
    history: list[EarningsMove] = []
    if as_of is not None:
        close = df["close"].astype(float)
        idx = [pd.Timestamp(t).date() for t in close.index]
        pos_of = {d: k for k, d in enumerate(idx)}
        past = [d for d in events.earnings.get(symbol, ()) if d <= as_of][-EARNINGS_HISTORY:]
        for d in past:
            k = pos_of.get(d)
            move = _ratio(float(close.iloc[k]), float(close.iloc[k - 1])) if k else None
            history.append(EarningsMove(date=d, move=move))
    earnings = EarningsOut(next=nxt, held_through=pos is not None and nxt is not None,
                           history=history)  # fmt: skip

    fills = s.scalars(
        select(FillRow)
        .where(FillRow.mode == mode, FillRow.symbol == symbol)
        .order_by(FillRow.filled_at.desc())
    ).all()
    return StockProfile(
        symbol=symbol, as_of=as_of, adjusted=adjusted, quote=_quote(df, periods),
        relation=relation, periods=periods, periods_qqq=periods_qqq, key_stats=key_stats,
        technicals=technicals, swing=swing,
        rank_history=[RankPoint(date=x.signal_date, rank=detail_of(x).rank_now(symbol))
                      for x in reversed(sigs) if x.detail is not None],
        earnings=earnings,
        holding_periods=[PeriodOut(start=a, end=b)
                         for a, b in an.holding_periods(held_by_day(snaps), symbol)],
        trades=[FillOut(filled_at=f.filled_at, symbol=f.symbol, side=f.side, qty=f.qty,
                        price=float(f.price), commission=float(f.commission)) for f in fills],
    )  # fmt: skip


def _diff(a: float | None, b: float | None) -> float | None:
    return a - b if a is not None and b is not None else None
