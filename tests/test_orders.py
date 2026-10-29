from decimal import Decimal

import pytest

from quant.core.models import Side
from quant.engine.orders import OrderPlanParams, plan_orders

D = Decimal
P = {"SPY": D("500"), "TLT": D("100"), "SHY": D("80")}


def test_target_shares_formula() -> None:
    # floor(100000 × 0.6 × 0.98 / 500) = 117；floor(100000 × 0.4 × 0.98 / 100) = 392
    orders = plan_orders({"SPY": 0.6, "TLT": 0.4}, {}, P, D("100000"), D("100000"))
    assert {(o.symbol, o.side, o.qty) for o in orders} == {
        ("SPY", Side.BUY, 117),
        ("TLT", Side.BUY, 392),
    }


def test_sells_before_buys_and_proceeds_fund_buys() -> None:
    # 全仓 SPY 换成全仓 TLT，现金只有 1000
    orders = plan_orders({"TLT": 1.0}, {"SPY": 198}, P, D("100000"), D("1000"))
    assert [(o.symbol, o.side) for o in orders] == [("SPY", Side.SELL), ("TLT", Side.BUY)]
    assert orders[0].qty == 198
    assert orders[1].qty == 980  # floor(98000 / 100)


def test_full_exit_ignores_thresholds() -> None:
    # 目标为 0 时，即使只剩 1 股（5 美元）也清仓
    orders = plan_orders({}, {"SHY": 1}, {"SHY": D("5")}, D("100000"), D("99995"))
    assert [(o.symbol, o.side, o.qty) for o in orders] == [("SHY", Side.SELL, 1)]


def test_small_adjustments_skipped() -> None:
    # 已持有 117 股 SPY（约 58.5%），目标 60%：偏差 < 2 个百分点，跳过
    assert plan_orders({"SPY": 0.6}, {"SPY": 117}, P, D("100000"), D("41500")) == []
    # 偏差大于 2 个百分点但金额 < 100 美元也跳过
    # SHY 目标 floor(39200 / 80) = 490 股，已持有 489：差 1 股 = 80 美元
    params = OrderPlanParams(min_weight_diff=D("0"))
    assert plan_orders({"SHY": 0.4}, {"SHY": 489}, P, D("100000"), D("60880"), params) == []


def test_full_position_not_rebalanced_daily() -> None:
    # 满仓目标 100%，实际持有约 98%（现金缓冲），不应被判定为偏离
    assert plan_orders({"TLT": 1.0}, {"TLT": 975}, P, D("100000"), D("2500")) == []
    # 价格变化导致实际权重偏离超过 2 个百分点时才再平衡
    orders = plan_orders({"TLT": 1.0}, {"TLT": 940}, P, D("100000"), D("6000"))
    assert [(o.symbol, o.side, o.qty) for o in orders] == [("TLT", Side.BUY, 40)]


def test_buys_capped_by_cash() -> None:
    # 现金不足以买满目标：按 价格 × (1 + 0.5%) 估算，最多买 floor(10000 / 100.5) = 99 股
    orders = plan_orders({"TLT": 1.0}, {}, P, D("100000"), D("10000"))
    assert [(o.symbol, o.qty) for o in orders] == [("TLT", 99)]


def test_no_change_no_orders() -> None:
    assert plan_orders({"TLT": 1.0}, {"TLT": 980}, P, D("100000"), D("2000")) == []


def test_validation() -> None:
    with pytest.raises(ValueError, match="缺少价格"):
        plan_orders({"QQQ": 1.0}, {}, P, D("100000"), D("100000"))
    with pytest.raises(ValueError, match="净值"):
        plan_orders({"SPY": 1.0}, {}, P, D("0"), D("0"))


def test_new_position_ignores_weight_threshold() -> None:
    # 持有型策略把 min_weight_diff 设为 0.10；新开仓目标 5% 仍应买入（与清仓一样不受该阈值限制）
    params = OrderPlanParams(min_weight_diff=Decimal("0.10"))
    orders = plan_orders(
        {"AAA": 0.05}, {}, {"AAA": Decimal("100")}, Decimal("100000"), Decimal("100000"), params
    )
    assert [(o.symbol, o.side, o.qty) for o in orders] == [("AAA", Side.BUY, 49)]
