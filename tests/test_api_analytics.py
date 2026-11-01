"""面板组合与个股指标的纯计算（#65 Task 4）。样本小、数值手算。"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.api import analytics as a
from quant.broker.sim import CostModel
from quant.core.settings import CostSettings


def _series(values: list[float], start: date = date(2026, 10, 1)) -> pd.Series:
    idx = pd.DatetimeIndex([pd.Timestamp(start) + pd.Timedelta(days=i) for i in range(len(values))])
    return pd.Series(values, index=idx, dtype=float)


def _finite_or_none(obj: object) -> bool:
    return all(v is None or math.isfinite(v) for v in vars(obj).values() if not isinstance(v, str))


def test_summary_basic() -> None:
    nav = _series([100, 110, 99])
    qqq = _series([10, 11, 12])
    spy = _series([20, 20, 21])
    s = a.summary(nav, qqq, spy)
    assert s.today_pnl == pytest.approx(-11)
    assert s.today_return == pytest.approx(-0.1)
    assert s.total_return == pytest.approx(-0.01)
    assert s.qqq_return == pytest.approx(0.2)
    assert s.spy_return == pytest.approx(0.05)
    assert s.max_drawdown == pytest.approx(-0.1)
    assert s.current_drawdown == pytest.approx(-0.1)
    assert s.ann_vol is None and s.sharpe is None  # 收益率不足 20 个


def test_metrics_with_short_history() -> None:
    nav = _series([100])
    s = a.summary(nav, _series([10]), _series([20]))
    assert s.today_pnl is None and s.total_return is None and s.max_drawdown is None
    assert _finite_or_none(s)
    r = a.risk(nav, _series([20]), _series([10]))
    assert r.beta_spy is None and r.corr_qqq is None and r.var95_1d is None
    empty = a.summary(pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float))
    assert empty.total_return is None


def test_var_and_beta() -> None:
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 80)
    spy = _series(list(100 * np.cumprod(1 + r)))
    nav = _series(list(1000 * np.cumprod(1 + 2 * r)))
    qqq = _series(list(50 * np.cumprod(1 + r)))
    out = a.risk(nav, spy, qqq, window=60)
    assert out.beta_spy == pytest.approx(2.0, rel=1e-6)
    assert out.corr_qqq == pytest.approx(1.0, rel=1e-6)
    rets = nav.pct_change().dropna().iloc[-60:]
    assert out.var95_1d == pytest.approx(-float(rets.quantile(0.05)) * float(nav.iloc[-1]))
    s = a.summary(nav, qqq, spy)
    assert s.ann_vol is not None and s.sharpe is not None


def test_concentration() -> None:
    top5, top1 = a.concentration({"A": 0.3, "B": 0.2, "C": 0.1, "D": 0.1, "E": 0.05, "F": 0.05})
    assert (top5, top1) == pytest.approx((0.75, 0.3))
    assert a.concentration({}) == (0.0, 0.0)


def test_pnl_calendar_month() -> None:
    nav = _series([100, 102, 101, 105], start=date(2026, 9, 29))  # 9-29、9-30、10-01、10-02
    days = a.pnl_calendar(nav, 2026, 10)
    assert [d.date for d in days] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert days[0].pnl == pytest.approx(-1) and days[0].ret == pytest.approx(-1 / 102)
    assert days[1].pnl == pytest.approx(4)
    first = a.pnl_calendar(nav, 2026, 9)
    assert first[0].pnl is None and first[1].pnl == pytest.approx(2)


def test_contributions_uses_previous_qty() -> None:
    idx = pd.DatetimeIndex(["2026-10-01", "2026-10-02", "2026-10-05"])
    qty = pd.DataFrame({"A": [10, 10, 0], "B": [0, 5, 5]}, index=idx)
    close = pd.DataFrame({"A": [100.0, 103.0, 101.0], "B": [50.0, 52.0, 55.0]}, index=idx)
    got = a.contributions(qty, close, date(2026, 10, 2), date(2026, 10, 5))
    # 10-02：A 前一日 10 股 × 3 = 30，B 前一日 0；10-05：A 10 股 × (−2) = −20，B 5 股 × 3 = 15
    assert got == {"A": pytest.approx(10.0), "B": pytest.approx(15.0)}
    only_last = a.contributions(qty, close, date(2026, 10, 5), date(2026, 10, 5))
    assert only_last == {"A": pytest.approx(-20.0), "B": pytest.approx(15.0)}


@dataclass
class F:
    symbol: str
    side: str
    qty: int
    price: Decimal
    filled_at: datetime


def _at(d: int) -> datetime:
    return datetime(2026, 10, d, 15, tzinfo=UTC)


def test_closed_trades_fifo_partial() -> None:
    fills = [
        F("A", "BUY", 10, Decimal(100), _at(1)),
        F("A", "BUY", 10, Decimal(110), _at(2)),
        F("A", "SELL", 15, Decimal(120), _at(9)),
    ]
    trades = a.closed_trades(fills)
    assert [(t.qty, t.open_price, t.close_price) for t in trades] == [
        (10, 100.0, 120.0),
        (5, 110.0, 120.0),
    ]
    assert [t.pnl for t in trades] == pytest.approx([200.0, 50.0])
    assert trades[0].days == 8 and trades[0].ret == pytest.approx(0.2)
    assert trades[0].close_date == date(2026, 10, 9)
    stats = a.trade_stats(trades)
    assert stats.count == 2 and stats.win_rate == 1.0 and stats.avg_pnl == pytest.approx(125.0)
    assert a.trade_stats([]).win_rate is None


def test_holding_periods_split_on_gap() -> None:
    d = [date(2026, 10, i) for i in (1, 2, 5, 6)]
    snaps = {d[0]: {"A"}, d[1]: {"A", "B"}, d[2]: {"B"}, d[3]: {"A", "B"}}
    assert a.holding_periods(snaps, "A") == [(d[0], d[1]), (d[3], d[3])]
    assert a.holding_days(snaps, "A") == 1
    assert a.holding_days(snaps, "B") == 3
    assert a.holding_days(snaps, "C") == 0


@pytest.mark.parametrize(
    ("sleeve", "close", "ma10", "rank", "want"),
    [
        ("swing", 104.0, 100.0, None, "ok"),
        ("swing", 101.0, 100.0, None, "near"),
        ("swing", 99.0, 100.0, None, "exit"),
        ("swing", None, 100.0, None, None),
        ("momentum", None, None, 8, "ok"),
        ("momentum", None, None, 9, "near"),
        ("momentum", None, None, 10, "near"),
        ("momentum", None, None, 11, "exit"),
        ("momentum", None, None, None, "exit"),
        ("cash", None, None, None, None),
    ],
)
def test_signal_state_thresholds(
    sleeve: str, close: float | None, ma10: float | None, rank: int | None, want: str | None
) -> None:
    assert a.signal_state(sleeve, close, ma10, rank, top_n=10) == want


@pytest.mark.parametrize(("qty", "price"), [(1, 50.0), (300, 130.5), (5000, 2.0)])
def test_estimate_commission_matches_cost_model(qty: int, price: float) -> None:
    costs = CostSettings(
        commission_per_share=Decimal("0.0039"), min_commission=Decimal("0.99"),
        platform_per_share=Decimal("0.004"), platform_min=Decimal("1"),
        max_fee_pct=Decimal("0.005"),
    )  # fmt: skip
    want = CostModel.from_settings(costs).commission(qty, Decimal(str(price)) * qty)
    assert a.estimate_commission(qty, price, costs) == pytest.approx(float(want), abs=0.006)
    assert a.estimate_commission(qty, price, CostSettings()) is None  # 佣金未配置


def test_period_returns() -> None:
    days = pd.bdate_range("2025-01-02", "2026-10-30")
    close = pd.Series(np.linspace(100, 200, len(days)), index=days)
    got = a.period_returns(close, date(2026, 10, 30))
    assert set(got) == {"1D", "1W", "1M", "3M", "6M", "YTD", "1Y"}
    assert got["1D"] == pytest.approx(close.iloc[-1] / close.iloc[-2] - 1)
    ytd_base = close[close.index <= pd.Timestamp("2025-12-31")].iloc[-1]
    assert got["YTD"] == pytest.approx(close.iloc[-1] / ytd_base - 1)
    short = a.period_returns(close.iloc[-3:], date(2026, 10, 30))
    assert short["1Y"] is None and short["1D"] is not None


def test_atr_and_rsi() -> None:
    close = pd.Series(np.linspace(100, 120, 40))
    high, low = close + 1, close - 1
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    assert a.atr_pct(high, low, close, 14) == pytest.approx(
        float(tr.iloc[-14:].mean() / close.iloc[-1])
    )
    assert a.atr_pct(high.iloc[:5], low.iloc[:5], close.iloc[:5], 14) is None
    assert a.last(a.rsi(close, 14)) == pytest.approx(100.0)
    assert a.last(pd.Series([np.nan])) is None
