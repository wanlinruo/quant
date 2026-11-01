"""回测交易切分与子仓拆分（#67 B 批 Task 3）。手算期望值。"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from quant.core.models import Fill, Side
from quant.engine.ledger import build_ledger
from quant.strategies.base import Attribution

D = [
    date(2026, 3, 2),
    date(2026, 3, 3),
    date(2026, 3, 4),
    date(2026, 3, 5),
    date(2026, 3, 6),
    date(2026, 3, 9),
]


def fill(day: date, sym: str, side: Side, qty: int, px: str, comm: str = "0") -> Fill:
    return Fill(
        "o",
        sym,
        side,
        qty,
        Decimal(px),
        Decimal(comm),
        datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC),
    )


def attr(
    sleeves: dict[str, dict[str, float]],
    exits: dict[str, str] | None = None,
    default: dict[str, str] | None = None,
    factor: float | None = None,
) -> Attribution:
    return Attribution(
        sleeves=sleeves, timing_factor=factor, exits=exits or {}, default_exit=default or {}
    )


EQ = pd.Series(10_000.0, index=pd.DatetimeIndex(D))


def test_single_sleeve_one_trade_with_adjustments() -> None:
    holdings = {D[0]: {}, D[1]: {"AAA": (100, 1000.0)}, D[2]: {"AAA": (150, 1800.0)},
                D[3]: {"AAA": (100, 1100.0)}, D[4]: {}, D[5]: {}}  # fmt: skip
    fills = [
        fill(D[1], "AAA", Side.BUY, 100, "10", "1"),
        fill(D[2], "AAA", Side.BUY, 50, "12", "1"),
        fill(D[3], "AAA", Side.SELL, 50, "11", "1"),
        fill(D[4], "AAA", Side.SELL, 100, "13", "1"),
    ]
    a = {d: attr({"main": {"AAA": 1.0}}) for d in D[:3]} | {
        D[3]: attr({"main": {}}),
        D[4]: attr({"main": {}}),
    }
    led = build_ledger(holdings, fills, a, EQ)
    assert len(led.trades) == 1
    t = led.trades[0]
    assert (t.symbol, t.sleeve, t.open_date, t.close_date) == ("AAA", "main", D[1], D[4])
    assert (
        t.open_price == pytest.approx(10)
        and t.close_price == pytest.approx(13)
        and t.qty == pytest.approx(100)
    )
    assert t.pnl == pytest.approx(246) and t.ret == pytest.approx(246 / 1800)
    assert [(x["date"], x["qty"]) for x in t.adjustments] == [
        (D[2].isoformat(), 50),
        (D[3].isoformat(), -50),
    ]
    assert t.exit_reason == "目标调整"


def test_two_sleeves_same_symbol_split() -> None:
    holdings = {D[0]: {}, D[1]: {"BBB": (200, 2000.0)}, D[2]: {"BBB": (200, 2200.0)},
                D[3]: {"BBB": (100, 1200.0)}, D[4]: {}, D[5]: {}}  # fmt: skip
    fills = [fill(D[1], "BBB", Side.BUY, 200, "10"), fill(D[3], "BBB", Side.SELL, 100, "12"),
             fill(D[4], "BBB", Side.SELL, 100, "13")]  # fmt: skip
    both = attr(
        {"momentum": {"BBB": 0.2}, "swing": {"BBB": 0.2}}, default={"momentum": "动量排名跌出前 4"}
    )
    a = {D[0]: both, D[1]: both,
         D[2]: attr({"momentum": {"BBB": 0.2}, "swing": {}}, exits={"BBB": "跌破支撑线"},
                    default={"momentum": "动量排名跌出前 4"}),
         D[3]: attr({"momentum": {}, "swing": {}}, default={"momentum": "动量排名跌出前 4"}),
         D[4]: attr({"momentum": {}, "swing": {}})}  # fmt: skip
    led = build_ledger(holdings, fills, a, EQ)
    by = {t.sleeve: t for t in led.trades}
    assert set(by) == {"momentum", "swing"}
    sw, mo = by["swing"], by["momentum"]
    assert sw.close_date == D[3] and sw.exit_reason == "跌破支撑线" and sw.pnl == pytest.approx(200)
    assert sw.qty == pytest.approx(100) and sw.close_price == pytest.approx(12)
    assert (
        mo.close_date == D[4]
        and mo.exit_reason == "动量排名跌出前 4"
        and mo.pnl == pytest.approx(300)
    )
    assert sw.pnl + mo.pnl == pytest.approx(500)
    day2 = {(s, sl): w for d, s, sl, w in led.positions if d == D[2]}
    assert day2 == {("BBB", "momentum"): pytest.approx(0.11), ("BBB", "swing"): pytest.approx(0.11)}
    sleeve_total = sum(sum(x[3].values()) for x in led.daily)
    assert sleeve_total == pytest.approx(500)


def test_open_at_end_and_daily_rows() -> None:
    holdings: dict[date, dict[str, tuple[float, float]]] = {d: {} for d in D[:2]}
    holdings |= {d: {"CCC": (10, 100.0 + i)} for i, d in enumerate(D[2:])}
    fills = [fill(D[2], "CCC", Side.BUY, 10, "10")]
    a = {d: attr({"main": {"CCC": 0.5}}, factor=0.5) for d in D}
    led = build_ledger(holdings, fills, a, EQ)
    t = led.trades[0]
    assert t.close_date is None and t.close_price is None and t.exit_reason is None
    assert t.pnl == pytest.approx(103.0 - 100.0)
    last = led.daily[-1]
    assert last[0] == D[5] and last[1] == pytest.approx(1 - 103.0 / 10_000) and last[2] == 0.5
    assert len(led.daily) == len(D)
