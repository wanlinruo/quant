"""风控规则（纯函数）：每条规则都有拒绝用例。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quant.core.calendar import TradingCalendar
from quant.core.models import OrderRequest, OrderType, Quote, Side, make_client_order_id
from quant.core.settings import ConfigError, Settings
from quant.engine.executor import limit_price
from quant.risk.rules import (
    RiskLimits,
    check_holdings,
    check_orders,
    check_quotes,
    check_signal_freshness,
    check_targets,
    whitelist_extra,
)

D = Decimal
DAY = date(2026, 10, 29)
LIMITS = RiskLimits(
    whitelist=frozenset({"SPY", "QQQ", "TLT", "GLD", "SHY"}),
    max_symbol_weight=1.0,
    max_total_weight=1.0,
    max_order_usd=D("150000"),
    max_daily_turnover=2.2,
)


def order(sym: str, side: Side, qty: int, px: str) -> OrderRequest:
    cid = make_client_order_id(DAY, "etf_momentum", sym, side, 1)
    return OrderRequest(cid, sym, side, qty, OrderType.LIMIT, D(px))


def rules(violations: list) -> list[str]:  # type: ignore[type-arg]
    return [v.rule for v in violations]


def test_from_settings_requires_order_cap() -> None:
    s = Settings(broker="tiger", risk={"symbol_whitelist": ["SPY"]})
    with pytest.raises(ConfigError, match="max_order_usd"):
        RiskLimits.from_settings(s)
    s = Settings(broker="tiger", risk={"max_order_usd": "1"})
    with pytest.raises(ConfigError, match="symbol_whitelist"):
        RiskLimits.from_settings(s)


def test_paper_config_has_confirmed_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from quant.core.settings import load_settings

    monkeypatch.setenv("TRADING_MODE", "paper")
    lim = RiskLimits.from_settings(load_settings())
    assert lim.max_order_usd == D("1050000")
    assert lim.max_daily_turnover == 2.2


def test_targets_ok() -> None:
    assert check_targets({"QQQ": 1.0}, LIMITS) == []
    assert check_targets({"SPY": 0.5, "TLT": 0.5}, LIMITS) == []
    assert check_targets({}, LIMITS) == []


def test_targets_whitelist() -> None:
    assert rules(check_targets({"TSLA": 1.0}, LIMITS)) == ["whitelist"]


def test_targets_symbol_weight() -> None:
    lim = RiskLimits(**{**LIMITS.__dict__, "max_symbol_weight": 0.5})
    assert rules(check_targets({"SPY": 0.6, "TLT": 0.4}, lim)) == ["symbol_weight"]
    assert rules(check_targets({"SPY": -0.1}, LIMITS)) == ["symbol_weight"]


def test_targets_total_weight() -> None:
    assert rules(check_targets({"SPY": 0.6, "TLT": 0.6}, LIMITS)) == ["total_weight"]


def test_holdings_whitelist() -> None:
    assert check_holdings({"SPY"}, LIMITS) == []
    assert rules(check_holdings({"SPY", "AAPL"}, LIMITS)) == ["whitelist"]


def _check(orders: list[OrderRequest], **kw: str) -> list[str]:
    args = {"net_liquidation": "100000", "available_cash": "100000", "traded_today": "0", **kw}
    return rules(
        check_orders(
            orders,
            LIMITS,
            net_liquidation=D(args["net_liquidation"]),
            available_cash=D(args["available_cash"]),
            traded_today=D(args["traded_today"]),
        )
    )


def test_orders_ok_full_rotation() -> None:
    batch = [order("SPY", Side.SELL, 160, "600"), order("QQQ", Side.BUY, 180, "530")]
    assert _check(batch, available_cash="98000") == []  # 卖 9.6 万 + 买 9.54 万，换手 1.95


def test_orders_whitelist() -> None:
    assert _check([order("AAPL", Side.BUY, 1, "200")]) == ["whitelist"]


def test_orders_cap() -> None:
    assert _check([order("SPY", Side.BUY, 300, "600")], available_cash="1000000") == ["order_usd"]


def test_orders_cash_no_margin() -> None:
    assert _check([order("SPY", Side.BUY, 100, "600")], available_cash="59999") == ["cash"]


def test_orders_turnover() -> None:
    batch = [order("SPY", Side.SELL, 160, "600")]
    assert _check(batch, traded_today="125000") == ["turnover"]  # (12.5 + 9.6) / 10 = 2.21


def test_orders_market_order_rejected() -> None:
    cid = make_client_order_id(DAY, "etf_momentum", "SPY", Side.BUY, 1)
    mkt = OrderRequest(cid, "SPY", Side.BUY, 1, OrderType.MARKET)
    assert _check([mkt]) == ["order_usd"]


def test_signal_freshness() -> None:
    cal = TradingCalendar()
    assert check_signal_freshness(date(2026, 10, 28), DAY, cal) == []
    assert rules(check_signal_freshness(date(2026, 10, 27), DAY, cal)) == ["stale"]
    # 周一执行，信号应截至上周五
    assert check_signal_freshness(date(2026, 10, 23), date(2026, 10, 26), cal) == []


def test_quotes_freshness() -> None:
    open_ = datetime(2026, 10, 29, 13, 30, tzinfo=UTC)
    fresh = Quote("SPY", D("600"), datetime(2026, 10, 29, 13, 35, tzinfo=UTC), delayed=True)
    old = Quote("QQQ", D("500"), datetime(2026, 10, 28, 20, 0, tzinfo=UTC), delayed=True)
    assert check_quotes({"SPY": fresh}, ["SPY"], open_) == []
    assert rules(check_quotes({"SPY": fresh, "QQQ": old}, ["SPY", "QQQ"], open_)) == ["stale"]
    assert rules(check_quotes({}, ["SPY"], open_)) == ["stale"]


def test_limit_price_rounding() -> None:
    off = D("0.002")
    assert limit_price(D("600.00"), Side.BUY, off) == D("601.20")
    assert limit_price(D("100.01"), Side.BUY, off) == D("100.22")  # 100.21002 → 向上
    assert limit_price(D("100.01"), Side.SELL, off) == D("99.80")  # 99.80998 → 向下
    assert limit_price(D("0.5"), Side.SELL, off) == D("0.4990")


# ---- 动态白名单（#47 第二份计划任务 6，safety） ----

# 2026-08-01 起 WDC、OLD 在名单中；
# 2026-08-20 OLD 被调出（距 DAY 70 天）；2026-09-19 GONE 被调出（距 DAY 40 天）
UNIVERSE_CSV = (
    "date,tickers\n"
    '2026-08-01,"AAPL,WDC,OLD,GONE"\n'
    '2026-08-20,"AAPL,WDC,GONE"\n'
    '2026-09-19,"AAPL,WDC"\n'
)


def _settings(tmp_path: Path, mode: str = "sp500", write: bool = True) -> Settings:
    if write:
        path = tmp_path / "universe" / "sp500_history.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(UNIVERSE_CSV, encoding="utf-8")
    return Settings(
        broker="tiger",
        data_dir=tmp_path,
        risk={"symbol_whitelist": ["QQQ"], "max_order_usd": "150000", "universe_whitelist": mode},
    )


def test_whitelist_extra_none(tmp_path: Path) -> None:
    s = _settings(tmp_path, mode="none")
    assert whitelist_extra(s, DAY) == frozenset()
    limits = RiskLimits.from_settings(s, whitelist_extra(s, DAY))
    assert rules(check_targets({"WDC": 0.1}, limits)) == ["whitelist"]  # 行为不变


def test_whitelist_extra_sp500(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    extra = whitelist_extra(s, DAY)
    limits = RiskLimits.from_settings(s, extra)
    assert check_targets({"WDC": 0.1, "QQQ": 0.5}, limits) == []
    assert check_holdings({"GONE"}, limits) == []  # 40 天前调出：仍在白名单
    assert rules(check_holdings({"OLD"}, limits)) == ["whitelist"]  # 70 天前调出


def test_whitelist_extra_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="成分名单"):
        whitelist_extra(_settings(tmp_path, write=False), DAY)
