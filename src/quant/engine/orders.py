"""目标仓位 → 订单（PROJECT_PLAN 6.3）。纯函数，回测与 Phase 4 的 Executor 共用。

1. 目标股数 = floor(净值 × 目标权重 × (1 − 现金缓冲) / 价格)
2. 与当前持仓做差得到买卖数量；先卖后买
3. 调整金额 < min_trade_usd，或当前权重与有效目标权重（目标权重 × (1 − 现金缓冲)）
   相差 < min_weight_diff 时跳过；
   但清仓（目标为 0）总是执行，避免残留碎仓
4. 买单受可用资金约束：现有现金 + 预计卖出所得，按 价格 × (1 + price_margin) 估算成本
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from quant.core.models import Side

_ONE = Decimal(1)


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    side: Side
    qty: int
    ref_price: Decimal

    @property
    def notional(self) -> Decimal:
        return self.ref_price * self.qty


@dataclass(frozen=True)
class OrderPlanParams:
    cash_buffer: Decimal = Decimal("0.02")
    min_trade_usd: Decimal = Decimal("100")
    min_weight_diff: Decimal = Decimal("0.02")
    # 估算买入成本时在参考价上加的余量（滑点、限价上浮、佣金），避免资金不足被拒
    price_margin: Decimal = Decimal("0.005")


def _floor_int(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


def plan_orders(
    target_weights: Mapping[str, float],
    positions: Mapping[str, int],
    prices: Mapping[str, Decimal],
    net_liquidation: Decimal,
    cash: Decimal,
    params: OrderPlanParams | None = None,
) -> list[PlannedOrder]:
    """返回按“先卖后买”排序的订单。prices 必须覆盖目标标的与当前持仓。"""
    params = params or OrderPlanParams()
    symbols = sorted(set(target_weights) | {s for s, q in positions.items() if q})
    missing = [s for s in symbols if s not in prices]
    if missing:
        raise ValueError(f"缺少价格：{missing}")
    if net_liquidation <= 0:
        raise ValueError(f"净值必须为正：{net_liquidation}")

    investable = net_liquidation * (_ONE - params.cash_buffer)
    sells: list[PlannedOrder] = []
    buys: list[PlannedOrder] = []
    for s in symbols:
        price = prices[s]
        if price <= 0:
            raise ValueError(f"{s} 价格非正：{price}")
        w = Decimal(str(target_weights.get(s, 0.0)))
        current = positions.get(s, 0)
        target = _floor_int(investable * w / price) if w > 0 else 0
        diff = target - current
        if diff == 0:
            continue
        closing = target == 0
        if not closing:
            # 与扣除现金缓冲后的有效目标权重比较，否则满仓持有时会每天被判定为偏离
            current_w = Decimal(current) * price / net_liquidation
            effective_w = w * (_ONE - params.cash_buffer)
            too_small = abs(diff) * price < params.min_trade_usd
            if too_small or abs(effective_w - current_w) < params.min_weight_diff:
                continue
        side = Side.BUY if diff > 0 else Side.SELL
        order = PlannedOrder(s, side, abs(diff), price)
        (buys if side is Side.BUY else sells).append(order)

    available = cash + sum((o.notional for o in sells), start=Decimal(0))
    capped: list[PlannedOrder] = []
    for o in sorted(buys, key=lambda o: o.notional, reverse=True):
        unit = o.ref_price * (_ONE + params.price_margin)
        qty = min(o.qty, _floor_int(available / unit)) if available > 0 else 0
        if qty <= 0 or qty * o.ref_price < params.min_trade_usd:
            continue
        capped.append(PlannedOrder(o.symbol, o.side, qty, o.ref_price))
        available -= unit * qty
    return sells + capped
