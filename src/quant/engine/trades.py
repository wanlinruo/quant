"""成交 → 逐笔交易（round trip），用于胜率、盈亏比、持有天数（swing_pullback 设计第 7 节）。"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from quant.core.calendar import get_calendar
from quant.core.models import Fill, Side

ET = ZoneInfo("America/New_York")
COLUMNS = ["symbol", "entry_day", "exit_day", "qty", "entry_price", "exit_price", "pnl",
           "return", "holding_days"]  # fmt: skip


@dataclass
class _Lot:
    day: object
    qty: int
    price: float
    fee_per_share: float


def round_trips(fills: Sequence[Fill]) -> pd.DataFrame:
    """按标的 FIFO 配对买卖；费用按股数分摊到每笔；未平仓部分不计入。"""
    cal = get_calendar()
    lots: dict[str, deque[_Lot]] = {}
    rows = []
    for f in sorted(fills, key=lambda x: x.filled_at):
        day = f.filled_at.astimezone(ET).date()
        fee_ps = float(f.commission) / f.qty
        if f.side is Side.BUY:
            lots.setdefault(f.symbol, deque()).append(_Lot(day, f.qty, float(f.price), fee_ps))
            continue
        remaining = f.qty
        queue = lots.get(f.symbol, deque())
        while remaining > 0 and queue:
            lot = queue[0]
            q = min(remaining, lot.qty)
            cost = lot.price * q
            pnl = (float(f.price) - lot.price) * q - (lot.fee_per_share + fee_ps) * q
            held = len(cal.trading_days(lot.day, day)) - 1  # type: ignore[arg-type]
            rows.append([f.symbol, lot.day, day, q, lot.price, float(f.price), pnl,
                         pnl / cost if cost else float("nan"), held])  # fmt: skip
            lot.qty -= q
            remaining -= q
            if lot.qty == 0:
                queue.popleft()
    return pd.DataFrame(rows, columns=COLUMNS)


def trade_stats(trips: pd.DataFrame) -> dict[str, float]:
    if trips.empty:
        return {"win_rate": float("nan"), "payoff": float("nan"),
                "avg_holding_days": float("nan"), "count": 0}  # fmt: skip
    wins = trips.loc[trips["pnl"] > 0, "pnl"]
    losses = trips.loc[trips["pnl"] < 0, "pnl"]
    payoff = wins.mean() / abs(losses.mean()) if len(wins) and len(losses) else float("nan")
    return {
        "win_rate": float(len(wins) / len(trips)),
        "payoff": float(payoff),
        "avg_holding_days": float(trips["holding_days"].mean()),
        "count": len(trips),
    }
