"""交易明细、等权基准、通过标准（swing_pullback Task 6）。"""

from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.core.models import Fill, Side
from quant.engine.evaluation import combo_checks, meets_criteria
from quant.engine.metrics import Metrics, equal_weight_rebalanced
from quant.engine.trades import round_trips, trade_stats

D = Decimal


def fill(day: date, side: Side, qty: int, price: str, fee: str = "1") -> Fill:
    at = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    return Fill("x", "AAA", side, qty, D(price), D(fee), at)


def test_round_trips_fifo() -> None:
    fills = [
        fill(date(2024, 3, 1), Side.BUY, 10, "100"),
        fill(date(2024, 3, 4), Side.BUY, 10, "110"),
        fill(date(2024, 3, 8), Side.SELL, 15, "120"),
        fill(date(2024, 3, 11), Side.SELL, 5, "90"),
    ]
    t = round_trips(fills)
    assert list(t["entry_day"]) == [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 4)]
    assert list(t["qty"]) == [10, 5, 5]
    # 第一笔：(120−100)×10 − 买入费 1 − 卖出费 1×10/15
    assert t["pnl"].iloc[0] == pytest.approx(200 - 1 - 10 / 15)
    # 第二笔：(120−110)×5 − 买入费 1×5/10 − 卖出费 1×5/15
    assert t["pnl"].iloc[1] == pytest.approx(50 - 0.5 - 5 / 15)
    # 第三笔：(90−110)×5 − 0.5 − 1
    assert t["pnl"].iloc[2] == pytest.approx(-100 - 0.5 - 1)
    assert t["return"].iloc[0] == pytest.approx((200 - 1 - 10 / 15) / 1000)
    assert list(t["holding_days"]) == [5, 4, 5]  # 交易日数（3-1 周五 → 3-8 周五 = 5）


def test_open_position_not_counted() -> None:
    assert round_trips([fill(date(2024, 3, 1), Side.BUY, 10, "100")]).empty


def test_trade_stats() -> None:
    trips = pd.DataFrame({"pnl": [10.0, 20.0, 30.0, -15.0], "holding_days": [2, 4, 6, 8]})
    s = trade_stats(trips)
    assert s["win_rate"] == pytest.approx(0.75)
    assert s["payoff"] == pytest.approx(20.0 / 15.0)
    assert s["avg_holding_days"] == pytest.approx(5.0)
    assert s["count"] == 4


def test_equal_weight_rebalanced() -> None:
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"])
    prices = pd.DataFrame(
        {"A": [1.0, 2.0, 2.0, 4.0, 4.0], "B": [1.0, 1.0, 1.0, 1.0, 2.0]}, index=idx
    )
    eq = equal_weight_rebalanced(prices, 100.0)
    # 1-31：A 翻倍 → 0.5×200% + 0.5×100% = 150；月末再平衡为各 75
    assert eq.loc["2024-01-31"] == pytest.approx(150.0)
    # 2-29：A 再翻倍 → 75×2 + 75 = 225；再平衡为各 112.5
    assert eq.loc["2024-02-29"] == pytest.approx(225.0)
    # 3-01：B 翻倍 → 112.5 + 225 = 337.5
    assert eq.loc["2024-03-01"] == pytest.approx(337.5)


def m(cagr: float, mdd: float, sharpe: float) -> Metrics:
    return Metrics("", "", 0.0, cagr, 0.2, mdd, sharpe, 0.0, 0.0, 0)


def test_meets_criteria_boundaries() -> None:
    qqq, eq = m(0.20, -0.35, 0.90), m(0.25, -0.40, 0.95)
    ok = meets_criteria(m(0.16, -0.21, 0.95), qqq, eq)  # 年化 = 0.8×、回撤 = 0.6×、夏普 = 等权
    assert ok.passed
    assert not meets_criteria(m(0.159, -0.21, 0.95), qqq, eq).cagr
    assert not meets_criteria(m(0.16, -0.2101, 0.95), qqq, eq).drawdown
    assert not meets_criteria(m(0.16, -0.21, 0.90), qqq, eq).sharpe_vs_qqq  # 需严格大于
    assert not meets_criteria(m(0.16, -0.21, 0.94), qqq, eq).sharpe_vs_equal


def test_meets_criteria_negative_qqq_cagr() -> None:
    qqq, eq = m(-0.10, -0.35, -0.2), m(-0.12, -0.40, -0.3)
    assert meets_criteria(m(-0.08, -0.20, 0.1), qqq, eq).cagr  # −0.08 ≥ −0.08
    assert not meets_criteria(m(-0.09, -0.20, 0.1), qqq, eq).cagr  # 照字面：−0.09 < −0.08


def test_nan_metrics_fail() -> None:
    qqq, eq = m(0.20, -0.35, 0.90), m(0.25, -0.40, 0.95)
    assert not meets_criteria(m(float("nan"), -0.2, 1.0), qqq, eq).passed
    assert np.isnan(float("nan"))


def test_combo_checks_boundaries() -> None:
    qqq = m(0.19, -0.35, 0.90)
    full = m(0.22, -0.25, 1.1)  # 恰好在门槛上
    ins, oos = m(0.2, -0.2, 1.0), m(0.3, -0.2, 1.5)
    q_ins, q_oos = m(0.14, -0.35, 0.67), m(0.31, -0.23, 1.45)
    ok = [m(0.2, -0.30, 1.0), m(0.25, -0.28, 1.2)]
    got = combo_checks(full, qqq, ins, q_ins, oos, q_oos, ok)
    assert got == {
        "最大回撤 ≥ -25%": True,
        "年化 ≥ QQQ + 3 个百分点": True,
        "夏普 ≥ 1.1": True,
        "两个区间夏普都高于 QQQ": True,
        "相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0": True,
    }
    bad = combo_checks(full, qqq, ins, q_ins, oos, q_oos, [*ok, m(0.3, -0.31, 1.3)])
    assert not bad["相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0"]
    assert not combo_checks(full, qqq, ins, q_ins, oos, q_oos, [])[
        "相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0"
    ]


def test_combo_checks_failures() -> None:
    qqq = m(0.19, -0.35, 0.90)
    q_ins, q_oos = m(0.14, -0.35, 0.67), m(0.31, -0.23, 1.45)
    got = combo_checks(m(0.21, -0.26, 1.09), qqq, m(0.2, -0.2, 0.6), q_ins,
                       m(0.3, -0.2, 1.5), q_oos, [m(0.2, -0.2, 1.0)])  # fmt: skip
    assert got["最大回撤 ≥ -25%"] is False
    assert got["年化 ≥ QQQ + 3 个百分点"] is False
    assert got["夏普 ≥ 1.1"] is False
    assert got["两个区间夏普都高于 QQQ"] is False
