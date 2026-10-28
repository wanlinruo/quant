"""TigerBroker：用假的 SDK 客户端测试映射与安全检查，不访问网络。"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS
from typing import Any

import pandas as pd
import pytest

from quant.broker.tiger import BrokerError, TigerBroker
from quant.core import safety
from quant.core.models import OrderRequest, OrderState, OrderType, Side, make_client_order_id
from quant.core.safety import OrderNotAllowedError

PAPER = "2" * 17
LIVE = "U9876543"
D = Decimal


def status(name: str) -> NS:
    return NS(name=name)


def tiger_order(**kw: Any) -> NS:
    base = dict(
        id=123, contract=NS(symbol="SPY"), action="BUY", quantity=10, filled=0,
        avg_fill_price=0, limit_price=500.0, status=status("NEW"), reason="",
        user_mark="2026-10-28-etf_momentum-SPY-BUY-1", order_time=1761670000000,
        trade_time=None, update_time=None,
    )  # fmt: skip
    base.update(kw)
    return NS(**base)


class FakeTrade:
    def __init__(self) -> None:
        self.placed: list[Any] = []
        self.cancelled: list[int] = []
        self.orders: dict[int, NS] = {123: tiger_order()}
        self.error: Exception | None = None

    def get_prime_assets(self, base_currency: str) -> NS:
        seg = NS(cash_balance=1000.5, net_liquidation=100000.25, buying_power=4000, currency="USD")
        return NS(segments={"S": seg})

    def get_positions(self, sec_type: Any) -> list[NS]:
        return [
            NS(
                contract=NS(symbol="QQQ"),
                quantity=10,
                position_qty=10,
                average_cost=500.1,
                market_value=5100,
            ),
            NS(
                contract=NS(symbol="TLT"),
                quantity=0,
                position_qty=0,
                average_cost=0,
                market_value=0,
            ),
        ]

    def place_order(self, order: Any) -> int:
        if self.error:
            raise self.error
        self.placed.append(order)
        return 456

    def get_order(self, id: int) -> NS | None:
        return self.orders.get(id)

    def cancel_order(self, id: int) -> int:
        self.cancelled.append(id)
        return id

    def get_open_orders(self, **kw: Any) -> list[NS]:
        return list(self.orders.values())


class FakeQuote:
    def get_stock_delay_briefs(self, symbols: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"symbol": s, "close": 500.0, "pre_close": 499.0, "time": 1761670000000, "halted": 0.0}
             for s in symbols]
        )  # fmt: skip

    def get_stock_briefs(self, symbols: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"symbol": s, "latest_price": 500.0, "bid_price": 499.9, "ask_price": 500.1,
              "latest_time": 1761670000000} for s in symbols]
        )  # fmt: skip


@pytest.fixture(autouse=True)
def _server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety, "is_dev_machine", lambda: False)


def broker(mode: str = "paper", account: str = PAPER, **kw: Any) -> tuple[TigerBroker, FakeTrade]:
    trade = FakeTrade()
    return TigerBroker(mode, account, trade, FakeQuote(), **kw), trade  # type: ignore[arg-type]


def req(limit: str | None = "500.12", side: Side = Side.BUY) -> OrderRequest:
    cid = make_client_order_id(date(2026, 10, 28), "etf_momentum", "SPY", side, 1)
    if limit is None:
        return OrderRequest(cid, "SPY", side, 10, OrderType.MARKET)
    return OrderRequest(cid, "SPY", side, 10, limit_price=D(limit))


class TestSafety:
    def test_sim_mode_refused(self) -> None:
        with pytest.raises(OrderNotAllowedError):
            broker("sim")

    def test_paper_mode_requires_paper_account(self) -> None:
        with pytest.raises(OrderNotAllowedError) as e:
            broker("paper", LIVE)
        assert LIVE not in str(e.value)

    def test_live_refused_on_dev_machine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(safety, "is_dev_machine", lambda: True)
        with pytest.raises(OrderNotAllowedError):
            broker("live", LIVE)

    def test_place_order_rechecks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        b, trade = broker()
        b._mode = "sim"  # 模拟运行中模式被改动
        with pytest.raises(OrderNotAllowedError):
            b.place_order(req())
        assert trade.placed == []

    def test_errors_redact_account(self) -> None:
        b, trade = broker()
        trade.error = RuntimeError(f"account {PAPER} insufficient funds")
        with pytest.raises(BrokerError) as e:
            b.place_order(req())
        assert PAPER not in str(e.value)
        assert "***2222" in str(e.value)


class TestOrders:
    def test_limit_order_fields(self) -> None:
        b, trade = broker()
        assert b.place_order(req()) == "456"
        o = trade.placed[0]
        assert o.action == "BUY"
        assert o.quantity == 10
        assert o.limit_price == pytest.approx(500.12)
        assert o.time_in_force == "DAY"
        assert o.outside_rth is False  # SDK 默认允许盘前盘后，必须显式关闭
        assert o.user_mark == "2026-10-28-etf_momentum-SPY-BUY-1"

    def test_market_order(self) -> None:
        b, trade = broker()
        b.place_order(req(None, Side.SELL))
        assert trade.placed[0].order_type == "MKT"
        assert trade.placed[0].outside_rth is False

    def test_tick_size_enforced(self) -> None:
        b, trade = broker()
        with pytest.raises(ValueError, match="精度"):
            b.place_order(req("500.125"))
        assert trade.placed == []

    @pytest.mark.parametrize(
        ("tiger", "state"),
        [
            ("PENDING_NEW", OrderState.SUBMITTED),
            ("HELD", OrderState.SUBMITTED),
            ("PENDING_CANCEL", OrderState.SUBMITTED),
            ("PARTIALLY_FILLED", OrderState.PARTIALLY_FILLED),
            ("FILLED", OrderState.FILLED),
            ("CANCELLED", OrderState.CANCELLED),
            ("REJECTED", OrderState.REJECTED),
            ("EXPIRED", OrderState.EXPIRED),
        ],
    )
    def test_status_mapping(self, tiger: str, state: OrderState) -> None:
        b, trade = broker()
        filled = 10 if tiger == "FILLED" else (4 if tiger == "PARTIALLY_FILLED" else 0)
        trade.orders[123] = tiger_order(
            status=status(tiger), filled=filled, avg_fill_price=500.05 if filled else 0
        )
        st = b.get_order("123")
        assert st.state is state
        assert st.filled_qty == filled
        assert st.client_order_id == "2026-10-28-etf_momentum-SPY-BUY-1"
        assert (st.avg_fill_price is None) == (filled == 0)

    def test_unknown_status_raises(self) -> None:
        b, trade = broker()
        trade.orders[123] = tiger_order(status=status("WEIRD"))
        with pytest.raises(BrokerError, match="未知订单状态"):
            b.get_order("123")

    def test_missing_order(self) -> None:
        b, _ = broker()
        with pytest.raises(BrokerError, match="不存在"):
            b.get_order("999")

    def test_cancel_and_open_orders(self) -> None:
        b, trade = broker()
        b.cancel_order("123")
        assert trade.cancelled == [123]
        assert [o.order_id for o in b.get_open_orders()] == ["123"]


class TestAccountAndQuotes:
    def test_account(self) -> None:
        b, _ = broker()
        a = b.get_account()
        assert a.cash == D("1000.5")
        assert a.net_liquidation == D("100000.25")

    def test_positions_skip_zero(self) -> None:
        b, _ = broker()
        pos = b.get_positions()
        assert list(pos) == ["QQQ"]
        assert pos["QQQ"].qty == 10
        assert pos["QQQ"].avg_cost == D("500.1")

    def test_short_position_rejected(self) -> None:
        b, trade = broker()
        trade.get_positions = lambda sec_type: [  # type: ignore[method-assign]
            NS(
                contract=NS(symbol="SPY"),
                quantity=-1,
                position_qty=-1,
                average_cost=1,
                market_value=-1,
            )
        ]
        with pytest.raises(BrokerError, match="空头"):
            b.get_positions()

    def test_delayed_quotes_have_no_bid_ask(self) -> None:
        b, _ = broker()
        q = b.get_quotes(["SPY", "QQQ"])
        assert set(q) == {"SPY", "QQQ"}
        assert q["SPY"].delayed and q["SPY"].bid is None
        assert q["SPY"].reference_price(Side.BUY) == D("500.0")

    def test_realtime_quotes(self) -> None:
        b, _ = broker(quote_source="realtime")
        q = b.get_quotes(["SPY"])["SPY"]
        assert not q.delayed
        assert q.reference_price(Side.BUY) == D("500.1")
