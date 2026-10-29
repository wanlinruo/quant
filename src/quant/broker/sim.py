"""SimBroker：回测用模拟撮合（PROJECT_PLAN 6.1、第 9 章）。

撮合规则：
- 下单后不立即成交，在下一根日线（on_bar / open_session）的开盘价撮合；同一根日线内先卖后买。
- 开盘后（open_session 与 close_session 之间）提交的订单可用 match_open 按当日开盘价撮合，
  用于“卖单成交后再按实际现金计算买单”。
- 成交价 = 开盘价 × (1 ± 滑点)，买入上浮、卖出下浮。
- 限价单为当日有效：滑点后的成交价劣于限价则不成交，状态为 EXPIRED。
- 费用 = 佣金 max(股数 × 每股佣金, 最低佣金) + 平台费 max(股数 × 每股平台费, 最低平台费)，
  两项各自不超过成交额 × max_fee_pct（老虎固定式套餐，见 CostSettings）。
- 买入资金不足、卖出超过持仓（不做空）时拒绝（REJECTED）；当日该标的没有日线时 EXPIRED。
- 只在本地内存中运行，不连接任何券商。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from quant.broker.base import Broker
from quant.core.models import (
    AccountSnapshot,
    Bar,
    Fill,
    OrderRequest,
    OrderState,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
)
from quant.core.settings import CostSettings

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
_PRICE_STEP = Decimal("0.0001")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class CostModel:
    commission_per_share: Decimal
    min_commission: Decimal
    slippage: Decimal = Decimal("0.0005")
    platform_per_share: Decimal = Decimal(0)
    platform_min: Decimal = Decimal(0)
    max_fee_pct: Decimal | None = None

    def __post_init__(self) -> None:
        names = ("commission_per_share", "min_commission", "slippage", "platform_per_share",
                 "platform_min")  # fmt: skip
        for name in names:
            v = getattr(self, name)
            if not isinstance(v, Decimal) or not v.is_finite() or v < 0:
                raise ValueError(f"{name} 必须是非负 Decimal：{v!r}")
        cap = self.max_fee_pct
        if cap is not None and (not isinstance(cap, Decimal) or not 0 < cap < 1):
            raise ValueError(f"max_fee_pct 必须在 (0, 1) 之间：{cap!r}")

    @classmethod
    def from_settings(cls, costs: CostSettings) -> CostModel:
        if costs.commission_per_share is None or costs.min_commission is None:
            raise ValueError(
                "未配置佣金（costs.commission_per_share / costs.min_commission），"
                "老虎费率见 configs/base.yaml（#5）"
            )
        return cls(
            costs.commission_per_share,
            costs.min_commission,
            costs.slippage,
            costs.platform_per_share,
            costs.platform_min,
            costs.max_fee_pct,
        )

    def to_settings(self) -> CostSettings:
        return CostSettings(
            commission_per_share=self.commission_per_share,
            min_commission=self.min_commission,
            slippage=self.slippage,
            platform_per_share=self.platform_per_share,
            platform_min=self.platform_min,
            max_fee_pct=self.max_fee_pct,
        )

    def exec_price(self, open_price: Decimal, side: Side) -> Decimal:
        factor = 1 + self.slippage if side is Side.BUY else 1 - self.slippage
        return (open_price * factor).quantize(_PRICE_STEP, ROUND_HALF_UP)

    def commission(self, qty: int, notional: Decimal | None = None) -> Decimal:
        """一笔订单的佣金 + 平台费；notional（成交额）给出时按 max_fee_pct 封顶。"""
        parts = [
            max(self.commission_per_share * qty, self.min_commission),
            max(self.platform_per_share * qty, self.platform_min)
            if self.platform_per_share or self.platform_min
            else Decimal(0),
        ]
        if self.max_fee_pct is not None and notional is not None:
            cap = notional * self.max_fee_pct
            parts = [min(p, cap) for p in parts]
        return sum(parts, Decimal(0)).quantize(_CENT, ROUND_HALF_UP)


class SimBroker(Broker):
    def __init__(self, cash: Decimal, costs: CostModel, start: date) -> None:
        if not isinstance(cash, Decimal) or cash < 0:
            raise ValueError(f"初始资金必须是非负 Decimal：{cash!r}")
        self._cash = cash
        self._costs = costs
        self._today = start
        self._last_bar_day: date | None = None
        self._bars_today: dict[str, Bar] | None = None  # 开盘后、收盘前的当日日线
        self._seq = 0
        self._qty: dict[str, int] = {}
        self._cost_basis: dict[str, Decimal] = {}  # 持仓总成本（不含佣金）
        self._last: dict[str, Decimal] = {}  # 最近收盘价
        self._orders: dict[str, OrderStatus] = {}
        self._pending: list[str] = []  # 待下一根日线撮合的订单号，按提交顺序
        self._client_ids: set[str] = set()
        self.fills: list[Fill] = []

    # ---- 回测驱动 ----

    @property
    def today(self) -> date:
        return self._today

    def on_bar(self, day: date, bars: dict[str, Bar]) -> list[Fill]:
        """推进到交易日 day：按开盘价撮合待成交订单，再用收盘价更新市值。返回当日成交。"""
        fills = self.open_session(day, bars)
        self.close_session()
        return fills

    def open_session(self, day: date, bars: dict[str, Bar]) -> list[Fill]:
        """开盘：推进到 day，按开盘价撮合此前提交的订单。之后可以继续下单并调用 match_open。"""
        if self._last_bar_day is not None and day <= self._last_bar_day:
            raise ValueError(f"日期必须递增：{day} ≤ {self._last_bar_day}")
        for sym, bar in bars.items():
            if bar.symbol != sym or bar.date != day:
                raise ValueError(f"日线与键或日期不一致：{sym} {bar.symbol} {bar.date}")
        self._today = self._last_bar_day = day
        self._bars_today = dict(bars)
        return self.match_open()

    def match_open(self) -> list[Fill]:
        """按当日开盘价撮合全部待成交订单，先卖后买（PROJECT_PLAN 6.3）。"""
        if self._bars_today is None:
            raise RuntimeError("尚未开盘：先调用 open_session")
        pending = [self._orders[oid] for oid in self._pending]
        self._pending = []
        pending.sort(key=lambda o: 0 if o.side is Side.SELL else 1)
        bars = self._bars_today
        return [f for o in pending if (f := self._match(o, bars.get(o.symbol))) is not None]

    def close_session(self) -> None:
        """收盘：用收盘价更新估值。"""
        if self._bars_today is None:
            raise RuntimeError("尚未开盘：先调用 open_session")
        for sym, bar in self._bars_today.items():
            self._last[sym] = bar.close
        self._bars_today = None

    def _match(self, order: OrderStatus, bar: Bar | None) -> Fill | None:
        at = datetime.combine(self._today, MARKET_OPEN, ET)
        if bar is None:
            self._finish(order, OrderState.EXPIRED, at, "当日无行情")
            return None
        price = self._costs.exec_price(bar.open, order.side)
        lmt = order.limit_price
        if lmt is not None and (
            (order.side is Side.BUY and price > lmt) or (order.side is Side.SELL and price < lmt)
        ):
            self._finish(order, OrderState.EXPIRED, at, f"开盘成交价 {price} 劣于限价 {lmt}")
            return None
        qty = order.qty
        gross = price * qty
        fee = self._costs.commission(qty, gross)
        if order.side is Side.BUY:
            if gross + fee > self._cash:
                self._finish(order, OrderState.REJECTED, at, "资金不足")
                return None
            self._cash -= gross + fee
            self._qty[order.symbol] = self._qty.get(order.symbol, 0) + qty
            self._cost_basis[order.symbol] = self._cost_basis.get(order.symbol, Decimal(0)) + gross
        else:
            held = self._qty.get(order.symbol, 0)
            if qty > held:
                self._finish(order, OrderState.REJECTED, at, f"卖出 {qty} 超过持仓 {held}")
                return None
            basis = self._cost_basis[order.symbol]
            self._cost_basis[order.symbol] = basis - basis * qty / held
            self._qty[order.symbol] = held - qty
            if self._qty[order.symbol] == 0:
                del self._qty[order.symbol], self._cost_basis[order.symbol]
            self._cash += gross - fee
        self._orders[order.order_id] = replace(
            order, filled_qty=qty, avg_fill_price=price, state=OrderState.FILLED, updated_at=at
        )
        fill = Fill(order.order_id, order.symbol, order.side, qty, price, fee, at)
        self.fills.append(fill)
        return fill

    def _finish(self, order: OrderStatus, state: OrderState, at: datetime, reason: str) -> None:
        self._orders[order.order_id] = replace(order, state=state, updated_at=at, reason=reason)

    def _now(self) -> datetime:
        return datetime.combine(self._today, MARKET_CLOSE, ET)

    # ---- Broker 接口 ----

    def get_account(self) -> AccountSnapshot:
        value = sum(
            (self._last[s] * q for s, q in self._qty.items()),
            start=Decimal(0),
        )
        return AccountSnapshot(
            cash=self._cash,
            net_liquidation=self._cash + value,
            buying_power=self._cash,
            timestamp=self._now(),
        )

    def get_positions(self) -> dict[str, Position]:
        return {
            s: Position(
                symbol=s,
                qty=q,
                avg_cost=(self._cost_basis[s] / q).quantize(_PRICE_STEP, ROUND_HALF_UP),
                market_value=self._last[s] * q,
            )
            for s, q in self._qty.items()
        }

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """回测中的报价为最近收盘价，没有买卖价。"""
        return {
            s: Quote(symbol=s, last=self._last[s], timestamp=self._now())
            for s in symbols
            if s in self._last
        }

    def place_order(self, req: OrderRequest) -> str:
        if req.client_order_id in self._client_ids:
            raise ValueError(f"重复的 client_order_id：{req.client_order_id}")
        self._seq += 1
        order_id = f"SIM-{self._seq}"
        self._client_ids.add(req.client_order_id)
        self._orders[order_id] = OrderStatus(
            order_id=order_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            filled_qty=0,
            state=OrderState.SUBMITTED,
            updated_at=self._now(),
            limit_price=req.limit_price if req.order_type is OrderType.LIMIT else None,
        )
        self._pending.append(order_id)
        return order_id

    def cancel_order(self, order_id: str) -> None:
        order = self.get_order(order_id)
        if order.state.is_terminal:
            return
        self._pending.remove(order_id)
        self._finish(order, OrderState.CANCELLED, self._now(), "用户撤单")

    def get_order(self, order_id: str) -> OrderStatus:
        try:
            return self._orders[order_id]
        except KeyError:
            raise KeyError(f"订单不存在：{order_id}") from None

    def get_open_orders(self) -> list[OrderStatus]:
        return [self._orders[oid] for oid in self._pending]
