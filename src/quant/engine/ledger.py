"""回测的逐笔交易与每日持仓（#67 B 批，设计 7.1）。纯函数，只依赖 pandas。

口径（用户已决定）：同一 (symbol, 子仓) 从建仓到清仓算一笔；中途加减仓是这笔交易的调整；
只减仓不清仓不产生平仓原因。同一只股票同时在多个子仓时按子仓分别成段，盈亏按各子仓的权重比例拆分。

子仓份额取最近一个信号日的归因（信号日 t 决定、t+1 开盘成交，所以第 d 天用 d 之前最近的信号日）。
每日盈亏 = 收盘市值 − 前一日市值 − 当日净买入额（成交额含佣金），按前一日份额拆分到子仓。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant.core.models import Fill, Side
from quant.strategies.base import Attribution

ET = ZoneInfo("America/New_York")
DEFAULT_REASON = "目标调整"
Holdings = Mapping[date, Mapping[str, tuple[float, float]]]  # 日期 → 代码 → (股数, 市值)


@dataclass
class TradeRow:
    seq: int
    symbol: str
    sleeve: str
    open_date: date
    open_price: float
    qty: float
    close_date: date | None = None
    close_price: float | None = None
    ret: float | None = None
    pnl: float = 0.0
    exit_reason: str | None = None
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    max_invested: float = 0.0


@dataclass(frozen=True)
class Ledger:
    trades: list[TradeRow]
    positions: list[tuple[date, str, str, float]]  # (日期, 代码, 子仓, 占净值权重)
    daily: list[
        tuple[date, float, float | None, dict[str, float]]
    ]  # (日期, 现金权重, 择时系数, 子仓盈亏)


def _shares(a: Attribution | None) -> dict[str, dict[str, float]]:
    """代码 → 子仓 → 份额（和为 1）。"""
    if a is None:
        return {}
    total: dict[str, float] = defaultdict(float)
    for sleeve in a.sleeves.values():
        for s, w in sleeve.items():
            total[s] += w
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for name, sleeve in a.sleeves.items():
        for s, w in sleeve.items():
            if w > 0 and total[s] > 0:
                out[s][name] = w / total[s]
    return dict(out)


def _flows(
    fills: Sequence[Fill],
) -> tuple[dict[date, dict[str, float]], dict[tuple[date, str, Side], float]]:
    """每日每只的净买入额（含佣金），以及按方向的成交均价。"""
    net: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    amt: dict[tuple[date, str, Side], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for f in fills:
        d = f.filled_at.astimezone(ET).date()
        gross, comm = float(f.price) * f.qty, float(f.commission)
        net[d][f.symbol] += gross + comm if f.side is Side.BUY else -(gross - comm)
        acc = amt[(d, f.symbol, f.side)]
        acc[0] += gross
        acc[1] += f.qty
    avg = {k: v[0] / v[1] for k, v in amt.items() if v[1]}
    return net, avg


def build_ledger(
    holdings: Holdings,
    fills: Sequence[Fill],
    attribution: Mapping[date, Attribution],
    equity: pd.Series,
) -> Ledger:
    days = sorted(holdings)
    signal_days = sorted(attribution)
    net, avg = _flows(fills)
    eq = {
        ts.date(): float(v)
        for ts, v in zip(pd.DatetimeIndex(equity.index), equity.to_numpy(), strict=True)
    }

    trades: list[TradeRow] = []
    open_: dict[tuple[str, str], TradeRow] = {}
    positions: list[tuple[date, str, str, float]] = []
    daily: list[tuple[date, float, float | None, dict[str, float]]] = []
    prev_hold: Mapping[str, tuple[float, float]] = {}
    prev_share: dict[str, dict[str, float]] = {}
    si = -1
    for d in days:
        while si + 1 < len(signal_days) and signal_days[si + 1] < d:
            si += 1
        sig = attribution[signal_days[si]] if si >= 0 else None
        shares = _shares(sig)
        hold = holdings[d]
        today_share: dict[str, dict[str, float]] = {}
        for s, (qty, _mv) in hold.items():
            if qty > 0:
                today_share[s] = shares.get(s) or prev_share.get(s) or {"main": 1.0}
        names = set(today_share) | {k[0] for k in open_}
        # 1) 开仓：(代码, 子仓) 份额由 0 变正
        for s in names:
            qty, mv = hold.get(s, (0.0, 0.0))
            for sleeve, w in today_share.get(s, {}).items():
                if (s, sleeve) not in open_ and w > 0 and qty > 0:
                    px = avg.get((d, s, Side.BUY), mv / qty)
                    t = TradeRow(0, s, sleeve, d, px, qty * w, max_invested=mv * w)
                    open_[(s, sleeve)] = t
                    trades.append(t)
        # 2) 每日盈亏，按前一日份额（当日新建仓用当日份额）拆分到子仓与在持交易
        sleeve_pnl: dict[str, float] = defaultdict(float)
        for s in names | set(prev_hold) | set(net.get(d, {})):
            mv = hold.get(s, (0.0, 0.0))[1]
            pnl = mv - prev_hold.get(s, (0.0, 0.0))[1] - net.get(d, {}).get(s, 0.0)
            split = prev_share.get(s) or today_share.get(s) or {"main": 1.0}
            for sleeve, w in split.items():
                sleeve_pnl[sleeve] += pnl * w
                held = open_.get((s, sleeve))
                if held is not None:
                    held.pnl += pnl * w
        # 3) 平仓（份额变 0 或股数为 0）与调整（在持期间股数变化）
        for (s, sleeve), t in list(open_.items()):
            qty, mv = hold.get(s, (0.0, 0.0))
            w = today_share.get(s, {}).get(sleeve, 0.0)
            if w <= 0 or qty <= 0:
                last_qty, last_mv = prev_hold.get(s, (0.0, 0.0))
                fallback = last_mv / last_qty if last_qty else t.open_price
                t.close_date = d
                t.close_price = avg.get((d, s, Side.SELL), fallback)
                reason = sig.exits.get(s) if sig is not None and sleeve != "momentum" else None
                default = sig.default_exit.get(sleeve) if sig is not None else None
                t.exit_reason = reason or default or DEFAULT_REASON
                del open_[(s, sleeve)]
                continue
            if t.open_date != d:
                prev_qty = prev_hold.get(s, (0.0, 0.0))[0]
                if abs(qty - prev_qty) > 1e-9:  # 只有真实成交（股数变化）才算调整
                    before = prev_qty * prev_share.get(s, {}).get(sleeve, 0.0)
                    t.adjustments.append({"date": d.isoformat(), "qty": round(qty * w - before, 6)})
            t.max_invested = max(t.max_invested, mv * w)
        e = eq.get(d) or 0.0
        invested = 0.0
        for s, sh in today_share.items():
            mv = hold[s][1]
            invested += mv
            for sleeve, w in sh.items():
                positions.append((d, s, sleeve, mv * w / e if e else 0.0))
        daily.append((d, 1 - invested / e if e else 1.0, sig.timing_factor if sig else None,
                      {k: v for k, v in sleeve_pnl.items()}))  # fmt: skip
        prev_hold, prev_share = hold, today_share
    trades.sort(key=lambda t: (t.open_date, t.symbol, t.sleeve))
    for i, t in enumerate(trades, 1):
        t.seq = i
        t.ret = t.pnl / t.max_invested if t.max_invested else None
    return Ledger(trades, positions, daily)
