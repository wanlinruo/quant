"""领域模型：与券商 SDK 无关，tigeropen 类型只出现在 broker/tiger.py、data/providers/tiger.py。

约定（PROJECT_PLAN 6.1）：
- 金额、价格用 Decimal（拒绝 float，避免二进制误差）；股数用 int（不支持碎股）；权重用 float。
- 时间戳必须带时区；交易日用 date（美东）。
- 所有模型不可变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

# 美股代码：大写字母开头，可含数字与 .（如 BRK.B）；不含 -，保证 client_order_id 可解析
_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.]{0,9}")
_STRATEGY_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LMT"
    MARKET = "MKT"


class OrderState(StrEnum):
    SUBMITTED = "SUBMITTED"  # 已提交、未成交（含券商的 NEW / HELD 等）
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}
)


def _check_symbol(symbol: str) -> None:
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f"非法代码：{symbol!r}")


def _check_money(name: str, v: Decimal | None, *, positive: bool = False) -> None:
    if v is None:
        return
    if not isinstance(v, Decimal):
        raise TypeError(f"{name} 必须是 Decimal，收到 {type(v).__name__}")
    if not v.is_finite():
        raise ValueError(f"{name} 不是有限数：{v}")
    if positive and v <= 0:
        raise ValueError(f"{name} 必须为正：{v}")


def _check_qty(name: str, v: int, *, positive: bool = False) -> None:
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} 必须是 int（不支持碎股），收到 {type(v).__name__}")
    if v < 0 or (positive and v == 0):
        raise ValueError(f"{name} 非法：{v}")


def _check_aware(name: str, v: datetime) -> None:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError(f"{name} 必须带时区")


@dataclass(frozen=True, slots=True)
class Bar:
    """日线。价格口径由数据源决定（存储层负责区分原始价与复权价）。"""

    symbol: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        _check_symbol(self.symbol)
        for name in ("open", "high", "low", "close"):
            _check_money(name, getattr(self, name), positive=True)
        _check_qty("volume", self.volume)
        if not (self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high):
            raise ValueError(f"{self.symbol} {self.date} OHLC 不一致")


@dataclass(frozen=True, slots=True)
class Quote:
    """报价。delayed=True 表示免费延迟行情（约 15 分钟），可能没有买卖价。"""

    symbol: str
    last: Decimal
    timestamp: datetime
    bid: Decimal | None = None
    ask: Decimal | None = None
    delayed: bool = False

    def __post_init__(self) -> None:
        _check_symbol(self.symbol)
        _check_money("last", self.last, positive=True)
        _check_money("bid", self.bid, positive=True)
        _check_money("ask", self.ask, positive=True)
        _check_aware("timestamp", self.timestamp)
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError(f"{self.symbol} 买价 {self.bid} 高于卖价 {self.ask}")

    def reference_price(self, side: Side) -> Decimal:
        """挂限价单的参考价：买用卖一价、卖用买一价，没有时用最新价（PROJECT_PLAN 6.3）。"""
        quote = self.ask if side is Side.BUY else self.bid
        return quote if quote is not None else self.last


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: int
    avg_cost: Decimal
    market_value: Decimal

    def __post_init__(self) -> None:
        _check_symbol(self.symbol)
        _check_qty("qty", self.qty)  # 不做空，持仓不为负
        _check_money("avg_cost", self.avg_cost)
        _check_money("market_value", self.market_value)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    cash: Decimal
    net_liquidation: Decimal
    buying_power: Decimal
    timestamp: datetime
    currency: str = "USD"

    def __post_init__(self) -> None:
        _check_money("cash", self.cash)
        _check_money("net_liquidation", self.net_liquidation)
        _check_money("buying_power", self.buying_power)
        _check_aware("timestamp", self.timestamp)


def make_client_order_id(
    trade_date: date, strategy: str, symbol: str, side: Side, attempt: int
) -> str:
    """确定性订单 ID：{trade_date}-{strategy}-{symbol}-{side}-{attempt}（PROJECT_PLAN 第 8 章）。

    同一交易日、策略、标的、方向、重试次数总是得到同一个 ID，重复提交可被识别。
    """
    if not _STRATEGY_RE.fullmatch(strategy):
        raise ValueError(f"策略名只能用小写字母、数字、下划线：{strategy!r}")
    _check_symbol(symbol)
    _check_qty("attempt", attempt, positive=True)
    return f"{trade_date.isoformat()}-{strategy}-{symbol}-{Side(side).value}-{attempt}"


_CLIENT_ORDER_ID_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<strategy>[^-]+)-(?P<symbol>[^-]+)"
    r"-(?P<side>BUY|SELL)-(?P<attempt>\d+)"
)


@dataclass(frozen=True, slots=True)
class ClientOrderKey:
    trade_date: date
    strategy: str
    symbol: str
    side: Side
    attempt: int


def parse_client_order_id(client_order_id: str) -> ClientOrderKey:
    m = _CLIENT_ORDER_ID_RE.fullmatch(client_order_id)
    if not m:
        raise ValueError(f"非法 client_order_id：{client_order_id!r}")
    key = ClientOrderKey(
        trade_date=date.fromisoformat(m["date"]),
        strategy=m["strategy"],
        symbol=m["symbol"],
        side=Side(m["side"]),
        attempt=int(m["attempt"]),
    )
    canonical = make_client_order_id(
        key.trade_date, key.strategy, key.symbol, key.side, key.attempt
    )
    if canonical != client_order_id:
        raise ValueError(f"非法 client_order_id：{client_order_id!r}")
    return key


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    qty: int
    order_type: OrderType = OrderType.LIMIT
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        _check_symbol(self.symbol)
        _check_qty("qty", self.qty, positive=True)
        _check_money("limit_price", self.limit_price, positive=True)
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            raise ValueError("限价单必须有 limit_price，市价单不得有 limit_price")
        key = parse_client_order_id(self.client_order_id)
        if key.symbol != self.symbol or key.side is not self.side:
            raise ValueError(f"client_order_id 与订单的代码或方向不一致：{self.client_order_id}")

    @property
    def notional(self) -> Decimal | None:
        """限价单的最大金额；市价单无法事先确定。"""
        return self.limit_price * self.qty if self.limit_price is not None else None


@dataclass(frozen=True, slots=True)
class OrderStatus:
    """券商侧订单状态快照（Broker.get_order 返回）。"""

    order_id: str
    client_order_id: str | None
    symbol: str
    side: Side
    qty: int
    filled_qty: int
    state: OrderState
    updated_at: datetime
    limit_price: Decimal | None = None
    avg_fill_price: Decimal | None = None
    reason: str = ""
    # 该订单累计佣金与费用；券商未返回时为 None
    commission: Decimal | None = None

    def __post_init__(self) -> None:
        _check_symbol(self.symbol)
        _check_qty("qty", self.qty, positive=True)
        _check_qty("filled_qty", self.filled_qty)
        if self.filled_qty > self.qty:
            raise ValueError(f"成交数量 {self.filled_qty} 超过委托数量 {self.qty}")
        _check_money("limit_price", self.limit_price, positive=True)
        _check_money("avg_fill_price", self.avg_fill_price, positive=True)
        _check_money("commission", self.commission)
        _check_aware("updated_at", self.updated_at)

    @property
    def remaining_qty(self) -> int:
        return self.qty - self.filled_qty


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    symbol: str
    side: Side
    qty: int
    price: Decimal
    commission: Decimal
    filled_at: datetime

    def __post_init__(self) -> None:
        _check_symbol(self.symbol)
        _check_qty("qty", self.qty, positive=True)
        _check_money("price", self.price, positive=True)
        _check_money("commission", self.commission)
        if self.commission < 0:
            raise ValueError(f"佣金不能为负：{self.commission}")
        _check_aware("filled_at", self.filled_at)

    @property
    def gross(self) -> Decimal:
        return self.price * self.qty
