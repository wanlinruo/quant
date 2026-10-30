from datetime import date
from decimal import Decimal

import pytest

from quant.broker.sim import CostModel, SimBroker
from quant.core.models import (
    Bar,
    OrderRequest,
    OrderState,
    OrderType,
    Side,
    make_client_order_id,
)
from quant.core.settings import CostSettings

D = Decimal
D0 = date(2026, 10, 27)
D1 = date(2026, 10, 28)
D2 = date(2026, 10, 29)
# 测试用费率，不代表老虎实际费率（issue #5 待确认）
COSTS = CostModel(commission_per_share=D("0.01"), min_commission=D("1"), slippage=D("0.0005"))


def bar(sym: str, day: date, o: str, c: str | None = None) -> Bar:
    op, cl = D(o), D(c or o)
    return Bar(sym, day, op, max(op, cl), min(op, cl), cl, 1000)


def req(
    sym: str, side: Side, qty: int, limit: str | None = None, attempt: int = 1, day: date = D0
) -> OrderRequest:
    cid = make_client_order_id(day, "test", sym, side, attempt)
    if limit is None:
        return OrderRequest(cid, sym, side, qty, OrderType.MARKET)
    return OrderRequest(cid, sym, side, qty, limit_price=D(limit))


def broker(cash: str = "10000") -> SimBroker:
    return SimBroker(D(cash), COSTS, D0)


class TestCostModel:
    def test_commission_min_applies(self) -> None:
        assert COSTS.commission(10) == D("1.00")  # 10 × 0.01 = 0.1 < 1

    def test_tiger_fixed_plan(self) -> None:
        # 佣金 0.0039/股 + 平台费 0.004/股（最低 1），各自不超过成交额 0.5%（#5）
        c = CostModel(D("0.0039"), D("0"), D("0"), D("0.004"), D("1"), D("0.005"))
        assert c.commission(100, D("50000")) == D("1.39")  # 0.39 + max(0.4, 1)
        assert c.commission(2000, D("1000000")) == D("15.80")  # 7.8 + 8
        # 低价股小单：平台费最低 1 美元被 0.5% 封顶（10 股 × 2 美元 = 20 → 封顶 0.1）
        assert c.commission(10, D("20")) == D("0.14")  # 0.039 + 0.1
        assert c.commission(100) == D("1.39")  # 未给成交额时不封顶

    def test_config_matches_tiger_plan(self) -> None:
        from quant.core.settings import load_settings

        c = CostModel.from_settings(load_settings().costs)
        assert c.commission(100, D("50000")) == D("1.39")
        assert c.commission(2000, D("1000000")) == D("15.80")

    def test_commission_per_share(self) -> None:
        assert COSTS.commission(250) == D("2.50")

    def test_commission_rounded_to_cent(self) -> None:
        c = CostModel(D("0.0049"), D("0.99"))
        assert c.commission(333) == D("1.63")  # 1.6317

    def test_slippage_direction(self) -> None:
        assert COSTS.exec_price(D("100"), Side.BUY) == D("100.0500")
        assert COSTS.exec_price(D("100"), Side.SELL) == D("99.9500")

    def test_zero_slippage(self) -> None:
        c = CostModel(D("0"), D("0"), D("0"))
        assert c.exec_price(D("123.45"), Side.BUY) == D("123.45")
        assert c.commission(100) == D("0")

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            CostModel(D("-0.01"), D("1"))

    def test_from_settings_requires_commission(self) -> None:
        with pytest.raises(ValueError, match="#5"):
            CostModel.from_settings(CostSettings())
        c = CostModel.from_settings(
            CostSettings(commission_per_share=D("0.005"), min_commission=D("1"))
        )
        assert c.slippage == D("0.0005")


class TestMatching:
    def test_fills_at_next_open_not_immediately(self) -> None:
        b = broker()
        oid = b.place_order(req("SPY", Side.BUY, 10))
        assert b.get_order(oid).state is OrderState.SUBMITTED
        assert b.get_positions() == {}
        assert [o.order_id for o in b.get_open_orders()] == [oid]

        fills = b.on_bar(D1, {"SPY": bar("SPY", D1, "500", "510")})
        assert len(fills) == 1
        f = fills[0]
        assert f.price == D("500.2500")
        assert f.commission == D("1.00")
        o = b.get_order(oid)
        assert o.state is OrderState.FILLED
        assert o.filled_qty == 10
        assert o.avg_fill_price == D("500.2500")
        assert b.get_open_orders() == []

        acct = b.get_account()
        assert acct.cash == D("10000") - D("5002.50") - D("1.00")
        assert acct.net_liquidation == acct.cash + D("5100")  # 按收盘价 510 估值
        pos = b.get_positions()["SPY"]
        assert pos.qty == 10
        assert pos.avg_cost == D("500.2500")
        assert pos.market_value == D("5100")

    def test_sell_before_buy_uses_proceeds(self) -> None:
        b = broker("6000")
        b.place_order(req("SPY", Side.BUY, 10))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        cash_after_buy = b.get_account().cash  # 约 996.5，不够直接买 TLT
        assert cash_after_buy < D("4000")
        # 先提交买单、后提交卖单，撮合时仍先卖后买
        buy = b.place_order(req("TLT", Side.BUY, 40, day=D1))
        sell = b.place_order(req("SPY", Side.SELL, 10, day=D1))
        b.on_bar(D2, {"SPY": bar("SPY", D2, "500"), "TLT": bar("TLT", D2, "100")})
        assert b.get_order(sell).state is OrderState.FILLED
        assert b.get_order(buy).state is OrderState.FILLED
        assert [f.side for f in b.fills[-2:]] == [Side.SELL, Side.BUY]
        assert set(b.get_positions()) == {"TLT"}

    def test_full_round_trip_cash(self) -> None:
        b = broker()
        b.place_order(req("GLD", Side.BUY, 10))
        b.on_bar(D1, {"GLD": bar("GLD", D1, "200")})
        b.place_order(req("GLD", Side.SELL, 10, day=D1))
        b.on_bar(D2, {"GLD": bar("GLD", D2, "200")})
        # 只损失滑点与两笔最低佣金：10×200×0.0005×2 + 2
        assert b.get_account().cash == D("10000") - D("2.00") - D("2.00")
        assert b.get_positions() == {}

    def test_partial_sell_keeps_avg_cost(self) -> None:
        b = SimBroker(D("100000"), CostModel(D("0"), D("0"), D("0")), D0)
        b.place_order(req("QQQ", Side.BUY, 10))
        b.on_bar(D1, {"QQQ": bar("QQQ", D1, "400")})
        b.place_order(req("QQQ", Side.SELL, 4, day=D1))
        b.on_bar(D2, {"QQQ": bar("QQQ", D2, "450")})
        pos = b.get_positions()["QQQ"]
        assert pos.qty == 6
        assert pos.avg_cost == D("400")

    def test_limit_buy_expires_when_open_above_limit(self) -> None:
        b = broker()
        oid = b.place_order(req("SPY", Side.BUY, 1, limit="500"))
        assert b.on_bar(D1, {"SPY": bar("SPY", D1, "500")}) == []  # 滑点后 500.25 > 500
        o = b.get_order(oid)
        assert o.state is OrderState.EXPIRED
        assert "限价" in o.reason
        assert b.get_open_orders() == []

    def test_limit_buy_fills_within_limit(self) -> None:
        b = broker()
        oid = b.place_order(req("SPY", Side.BUY, 1, limit="501"))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        assert b.get_order(oid).state is OrderState.FILLED

    def test_limit_sell_expires_when_open_below_limit(self) -> None:
        b = broker()
        b.place_order(req("SPY", Side.BUY, 1))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        oid = b.place_order(req("SPY", Side.SELL, 1, limit="500", day=D1))
        b.on_bar(D2, {"SPY": bar("SPY", D2, "500")})  # 滑点后 499.75 < 500
        assert b.get_order(oid).state is OrderState.EXPIRED
        assert b.get_positions()["SPY"].qty == 1

    def test_insufficient_cash_rejected(self) -> None:
        b = broker("1000")
        oid = b.place_order(req("SPY", Side.BUY, 2))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})  # 1000.5 + 1 > 1000
        o = b.get_order(oid)
        assert o.state is OrderState.REJECTED
        assert o.reason == "资金不足"
        assert b.get_account().cash == D("1000")

    def test_oversell_rejected(self) -> None:
        b = broker()
        oid = b.place_order(req("SPY", Side.SELL, 1))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        assert b.get_order(oid).state is OrderState.REJECTED

    def test_missing_bar_expires(self) -> None:
        b = broker()
        oid = b.place_order(req("GLD", Side.BUY, 1))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        assert b.get_order(oid).state is OrderState.EXPIRED


class TestOrderManagement:
    def test_cancel_before_fill(self) -> None:
        b = broker()
        oid = b.place_order(req("SPY", Side.BUY, 1))
        b.cancel_order(oid)
        assert b.get_order(oid).state is OrderState.CANCELLED
        assert b.on_bar(D1, {"SPY": bar("SPY", D1, "500")}) == []
        assert b.get_positions() == {}

    def test_cancel_terminal_is_noop(self) -> None:
        b = broker()
        oid = b.place_order(req("SPY", Side.BUY, 1))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        b.cancel_order(oid)
        assert b.get_order(oid).state is OrderState.FILLED

    def test_duplicate_client_order_id_rejected(self) -> None:
        b = broker()
        b.place_order(req("SPY", Side.BUY, 1))
        with pytest.raises(ValueError, match="重复"):
            b.place_order(req("SPY", Side.BUY, 1))
        b.place_order(req("SPY", Side.BUY, 1, attempt=2))

    def test_unknown_order(self) -> None:
        with pytest.raises(KeyError):
            broker().get_order("SIM-999")

    def test_dates_must_increase(self) -> None:
        b = broker()
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        with pytest.raises(ValueError, match="递增"):
            b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})

    def test_bar_date_mismatch(self) -> None:
        with pytest.raises(ValueError, match="不一致"):
            broker().on_bar(D1, {"SPY": bar("SPY", D2, "500")})

    def test_quotes_are_last_close(self) -> None:
        b = broker()
        assert b.get_quotes(["SPY"]) == {}
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500", "505")})
        q = b.get_quotes(["SPY", "QQQ"])
        assert set(q) == {"SPY"}
        assert q["SPY"].last == D("505")
        assert q["SPY"].bid is None


class TestSessionSteps:
    def test_buy_with_actual_cash_after_sell_at_same_open(self) -> None:
        b = broker("6000")
        b.place_order(req("SPY", Side.BUY, 10))
        b.on_bar(D1, {"SPY": bar("SPY", D1, "500")})
        b.place_order(req("SPY", Side.SELL, 10, day=D1))
        # 开盘：卖单先成交，再按实际现金提交买单，于同一开盘价成交
        fills = b.open_session(D2, {"SPY": bar("SPY", D2, "450"), "TLT": bar("TLT", D2, "100")})
        assert [f.side for f in fills] == [Side.SELL]
        cash = b.get_account().cash
        qty = int(cash / D("100.06"))
        b.place_order(req("TLT", Side.BUY, qty, day=D2))
        buys = b.match_open()
        assert [(f.symbol, f.qty, f.price) for f in buys] == [("TLT", qty, D("100.0500"))]
        assert buys[0].filled_at.date() == D2
        b.close_session()
        assert b.get_account().cash >= 0

    def test_steps_require_open_session(self) -> None:
        b = broker()
        with pytest.raises(RuntimeError):
            b.match_open()
        with pytest.raises(RuntimeError):
            b.close_session()
