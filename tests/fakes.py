"""测试共用的假券商、假时钟与通知记录器。"""

from datetime import datetime, timedelta
from decimal import Decimal

from quant.broker.base import Broker
from quant.broker.tiger import BrokerError
from quant.core.models import (
    AccountSnapshot,
    OrderRequest,
    OrderState,
    OrderStatus,
    Position,
    Quote,
    Side,
)
from quant.notify.base import Level

D = Decimal
T0 = datetime.fromisoformat("2026-10-29T13:50:00+00:00")  # 09:50 ET（夏令时）


class Clock:
    def __init__(self, t: datetime = T0) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class FakeBroker(Broker):
    """下单时按 behavior 决定结果：fill 立即按限价全部成交，partial 成交一半后挂着，
    hang 一直挂着直到撤单，reject 被拒，error 抛出 BrokerError（结果未知）。"""

    def __init__(self, clock: Clock, cash: str, positions: dict[str, int]) -> None:
        self.clock = clock
        self.cash = D(cash)
        self.pos = dict(positions)
        self.prices = {"SPY": D("600"), "QQQ": D("500"), "TLT": D("90"), "GLD": D("250"),
                       "SHY": D("82")}  # fmt: skip
        self.quote_time = clock.now() - timedelta(minutes=15)
        self.behavior: dict[str, list[str]] = {}
        self.orders: dict[str, OrderStatus] = {}
        self.placed: list[OrderRequest] = []
        self.cancelled: list[str] = []

    def get_account(self) -> AccountSnapshot:
        mv = sum((self.prices[s] * q for s, q in self.pos.items()), D(0))
        return AccountSnapshot(self.cash, self.cash + mv, self.cash, self.clock.now())

    def get_positions(self) -> dict[str, Position]:
        return {
            s: Position(s, q, self.prices[s], self.prices[s] * q) for s, q in self.pos.items() if q
        }

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: Quote(s, self.prices[s], self.quote_time, delayed=True) for s in symbols}

    def _fill(self, st: OrderStatus, qty: int) -> OrderStatus:
        assert st.limit_price is not None
        sign = 1 if st.side is Side.BUY else -1
        self.pos[st.symbol] = self.pos.get(st.symbol, 0) + sign * qty
        self.cash -= sign * st.limit_price * qty + D(1)
        filled = st.filled_qty + qty
        state = OrderState.FILLED if filled == st.qty else OrderState.PARTIALLY_FILLED
        return OrderStatus(st.order_id, st.client_order_id, st.symbol, st.side, st.qty, filled,
                           state, self.clock.now(), st.limit_price, st.limit_price,
                           commission=D(1))  # fmt: skip

    def place_order(self, req: OrderRequest) -> str:
        queue = self.behavior.get(req.symbol)
        how = queue.pop(0) if queue else "fill"
        if how == "error":
            raise BrokerError("网络超时")
        self.placed.append(req)
        oid = str(1000 + len(self.placed))
        st = OrderStatus(oid, req.client_order_id, req.symbol, req.side, req.qty, 0,
                         OrderState.SUBMITTED, self.clock.now(), req.limit_price)  # fmt: skip
        if how == "fill":
            st = self._fill(st, req.qty)
        elif how == "partial":
            st = self._fill(st, req.qty // 2)
        elif how == "reject":
            st = OrderStatus(oid, req.client_order_id, req.symbol, req.side, req.qty, 0,
                             OrderState.REJECTED, self.clock.now(), req.limit_price,
                             reason="资金不足")  # fmt: skip
        self.orders[oid] = st
        return oid

    def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)
        st = self.orders[order_id]
        if not st.state.is_terminal:
            self.orders[order_id] = OrderStatus(
                st.order_id, st.client_order_id, st.symbol, st.side, st.qty, st.filled_qty,
                OrderState.CANCELLED, self.clock.now(), st.limit_price, st.avg_fill_price,
                commission=st.commission,
            )  # fmt: skip

    def get_order(self, order_id: str) -> OrderStatus:
        return self.orders[order_id]

    def get_open_orders(self) -> list[OrderStatus]:
        return [o for o in self.orders.values() if not o.state.is_terminal]


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Level]] = []

    def send(self, title: str, body: str, level: Level = Level.INFO) -> None:
        self.sent.append((title, body, level))
