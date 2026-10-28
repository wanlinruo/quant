from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

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
    make_client_order_id,
    parse_client_order_id,
)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 29, 9, 50, tzinfo=ET)
D = Decimal


class TestClientOrderId:
    def test_format(self) -> None:
        cid = make_client_order_id(date(2026, 10, 29), "etf_momentum", "SPY", Side.BUY, 1)
        assert cid == "2026-10-29-etf_momentum-SPY-BUY-1"

    def test_deterministic(self) -> None:
        args = (date(2026, 10, 29), "etf_momentum", "QQQ", Side.SELL, 2)
        assert make_client_order_id(*args) == make_client_order_id(*args)

    def test_each_part_changes_id(self) -> None:
        base = (date(2026, 10, 29), "etf_momentum", "SPY", Side.BUY, 1)
        variants = [
            (date(2026, 10, 30), "etf_momentum", "SPY", Side.BUY, 1),
            (date(2026, 10, 29), "other", "SPY", Side.BUY, 1),
            (date(2026, 10, 29), "etf_momentum", "QQQ", Side.BUY, 1),
            (date(2026, 10, 29), "etf_momentum", "SPY", Side.SELL, 1),
            (date(2026, 10, 29), "etf_momentum", "SPY", Side.BUY, 2),
        ]
        ids = {make_client_order_id(*v) for v in [base, *variants]}
        assert len(ids) == 6

    def test_roundtrip(self) -> None:
        cid = make_client_order_id(date(2026, 1, 2), "etf_momentum", "BRK.B", Side.SELL, 3)
        key = parse_client_order_id(cid)
        assert key.trade_date == date(2026, 1, 2)
        assert key.strategy == "etf_momentum"
        assert key.symbol == "BRK.B"
        assert key.side is Side.SELL
        assert key.attempt == 3

    @pytest.mark.parametrize(
        ("strategy", "symbol", "attempt"),
        [
            ("etf-momentum", "SPY", 1),  # 策略名含 -
            ("ETF", "SPY", 1),
            ("etf_momentum", "spy", 1),
            ("etf_momentum", "BRK-B", 1),
            ("etf_momentum", "SPY", 0),
        ],
    )
    def test_invalid_parts(self, strategy: str, symbol: str, attempt: int) -> None:
        with pytest.raises(ValueError):
            make_client_order_id(date(2026, 10, 29), strategy, symbol, Side.BUY, attempt)

    @pytest.mark.parametrize(
        "cid",
        [
            "2026-10-29-etf_momentum-SPY-HOLD-1",
            "2026-10-29-etf_momentum-SPY-BUY-01",
            "2026-13-01-etf_momentum-SPY-BUY-1",
            "random",
        ],
    )
    def test_parse_rejects(self, cid: str) -> None:
        with pytest.raises(ValueError):
            parse_client_order_id(cid)


class TestOrderRequest:
    CID = "2026-10-29-etf_momentum-SPY-BUY-1"

    def test_limit_order(self) -> None:
        req = OrderRequest(self.CID, "SPY", Side.BUY, 10, limit_price=D("580.25"))
        assert req.order_type is OrderType.LIMIT
        assert req.notional == D("5802.50")

    def test_float_price_rejected(self) -> None:
        with pytest.raises(TypeError, match="Decimal"):
            OrderRequest(self.CID, "SPY", Side.BUY, 10, limit_price=580.25)  # type: ignore[arg-type]

    def test_fractional_qty_rejected(self) -> None:
        with pytest.raises(TypeError, match="碎股"):
            OrderRequest(self.CID, "SPY", Side.BUY, 1.5, limit_price=D("1"))  # type: ignore[arg-type]

    @pytest.mark.parametrize("qty", [0, -1])
    def test_non_positive_qty(self, qty: int) -> None:
        with pytest.raises(ValueError):
            OrderRequest(self.CID, "SPY", Side.BUY, qty, limit_price=D("1"))

    def test_limit_requires_price(self) -> None:
        with pytest.raises(ValueError, match="limit_price"):
            OrderRequest(self.CID, "SPY", Side.BUY, 1)
        with pytest.raises(ValueError, match="limit_price"):
            OrderRequest(self.CID, "SPY", Side.BUY, 1, OrderType.MARKET, D("1"))
        assert OrderRequest(self.CID, "SPY", Side.BUY, 1, OrderType.MARKET).notional is None

    def test_client_order_id_must_match(self) -> None:
        with pytest.raises(ValueError, match="不一致"):
            OrderRequest(self.CID, "QQQ", Side.BUY, 1, limit_price=D("1"))
        with pytest.raises(ValueError, match="不一致"):
            OrderRequest(self.CID, "SPY", Side.SELL, 1, limit_price=D("1"))

    def test_frozen(self) -> None:
        req = OrderRequest(self.CID, "SPY", Side.BUY, 1, limit_price=D("1"))
        with pytest.raises(FrozenInstanceError):
            req.qty = 2  # type: ignore[misc]


class TestQuote:
    def test_reference_price_uses_bid_ask(self) -> None:
        q = Quote("SPY", D("580"), NOW, bid=D("579.9"), ask=D("580.1"))
        assert q.reference_price(Side.BUY) == D("580.1")
        assert q.reference_price(Side.SELL) == D("579.9")

    def test_delayed_quote_falls_back_to_last(self) -> None:
        q = Quote("SPY", D("580"), NOW, delayed=True)
        assert q.reference_price(Side.BUY) == D("580")
        assert q.reference_price(Side.SELL) == D("580")

    def test_crossed_quote_rejected(self) -> None:
        with pytest.raises(ValueError, match="高于"):
            Quote("SPY", D("580"), NOW, bid=D("581"), ask=D("580"))

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="时区"):
            Quote("SPY", D("580"), datetime(2026, 10, 29, 9, 50))


def test_bar_ohlc_consistency() -> None:
    Bar("SPY", date(2026, 10, 28), D("10"), D("11"), D("9"), D("10.5"), 100)
    with pytest.raises(ValueError, match="OHLC"):
        Bar("SPY", date(2026, 10, 28), D("10"), D("10.2"), D("9"), D("10.5"), 100)


def test_position_and_account() -> None:
    Position("TLT", 100, D("88.5"), D("8900"))
    with pytest.raises(ValueError):
        Position("TLT", -1, D("88.5"), D("0"))
    acct = AccountSnapshot(D("1000"), D("1000000"), D("1000"), datetime.now(UTC))
    assert acct.currency == "USD"


def test_order_status() -> None:
    st = OrderStatus(
        "123", "2026-10-29-etf_momentum-SPY-BUY-1", "SPY", Side.BUY, 10, 4,
        OrderState.PARTIALLY_FILLED, NOW, limit_price=D("580"),
    )  # fmt: skip
    assert st.remaining_qty == 6
    assert not st.state.is_terminal
    assert OrderState.CANCELLED.is_terminal
    with pytest.raises(ValueError, match="超过"):
        OrderStatus("1", None, "SPY", Side.BUY, 1, 2, OrderState.FILLED, NOW)


def test_fill() -> None:
    f = Fill("1", "GLD", Side.BUY, 3, D("190.10"), D("1.99"), NOW)
    assert f.gross == D("570.30")
    with pytest.raises(ValueError, match="佣金"):
        Fill("1", "GLD", Side.BUY, 3, D("190.10"), D("-1"), NOW)


def test_decimal_arithmetic_is_exact() -> None:
    # float 下 0.1 * 3 != 0.3；Decimal 金额必须精确
    assert Fill("1", "SHY", Side.SELL, 3, D("0.1"), D("0"), NOW).gross == D("0.3")
