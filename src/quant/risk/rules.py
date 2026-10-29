"""风控规则（PROJECT_PLAN 6.4）。纯函数：返回违规列表，任一违规即拒绝整批订单。

本模块覆盖：标的白名单、单标的权重上限、总仓位上限（不用保证金）、单笔订单金额上限、
单日换手上限、信号数据新鲜度、报价新鲜度。
对账一致性、熔断开关、交易模式保护由 Executor 在下单流程中检查
（engine/reconcile.py、core/safety.py）。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from quant.core.calendar import TradingCalendar
from quant.core.models import OrderRequest, Quote, Side
from quant.core.settings import ConfigError, Settings

# 权重和允许的浮点误差
_WEIGHT_EPS = 1e-9


@dataclass(frozen=True)
class Violation:
    rule: str  # whitelist / symbol_weight / total_weight / order_usd / turnover / cash / stale
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.detail}"


@dataclass(frozen=True)
class RiskLimits:
    whitelist: frozenset[str]
    max_symbol_weight: float
    max_total_weight: float
    max_order_usd: Decimal
    max_daily_turnover: float

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskLimits:
        r = settings.risk
        if not r.symbol_whitelist:
            raise ConfigError("risk.symbol_whitelist 为空，拒绝执行")
        if r.max_order_usd is None:
            raise ConfigError(
                f"TRADING_MODE={settings.trading_mode} 未配置 risk.max_order_usd，拒绝执行"
            )
        return cls(
            whitelist=frozenset(r.symbol_whitelist),
            max_symbol_weight=r.max_symbol_weight,
            max_total_weight=r.max_total_weight,
            max_order_usd=r.max_order_usd,
            max_daily_turnover=r.max_daily_turnover,
        )


def summarize(violations: Iterable[Violation]) -> str:
    return "；".join(map(str, violations))


def check_targets(weights: Mapping[str, float], limits: RiskLimits) -> list[Violation]:
    """目标仓位：白名单、单标的上限、非负、权重和不超过上限。"""
    out: list[Violation] = []
    for s, w in sorted(weights.items()):
        if s not in limits.whitelist:
            out.append(Violation("whitelist", f"目标仓位含白名单外标的 {s}"))
        if w < 0:
            out.append(Violation("symbol_weight", f"{s} 权重为负 {w}（不做空）"))
        elif w > limits.max_symbol_weight + _WEIGHT_EPS:
            out.append(
                Violation("symbol_weight", f"{s} 权重 {w:.2%} 超过 {limits.max_symbol_weight:.0%}")
            )
    total = sum(weights.values())
    if total > limits.max_total_weight + _WEIGHT_EPS:
        out.append(
            Violation("total_weight", f"权重和 {total:.2%} 超过 {limits.max_total_weight:.0%}")
        )
    return out


def check_holdings(symbols: Collection[str], limits: RiskLimits) -> list[Violation]:
    """当前持仓中出现白名单外标的：不自动处理，等待人工。"""
    return [
        Violation("whitelist", f"持仓含白名单外标的 {s}，需人工处理")
        for s in sorted(symbols)
        if s not in limits.whitelist
    ]


def check_orders(
    orders: Iterable[OrderRequest],
    limits: RiskLimits,
    *,
    net_liquidation: Decimal,
    available_cash: Decimal,
    traded_today: Decimal,
) -> list[Violation]:
    """待提交的一批限价单。

    - available_cash：可用于买入的资金（现金 + 本批卖单按限价估算的所得）；买单按限价计算
      的总额不得超过它，即不使用保证金。
    - traded_today：当日已成交金额；加上本批金额后与净值之比不得超过换手上限。
    """
    orders = list(orders)
    out: list[Violation] = []
    if net_liquidation <= 0:
        return [Violation("cash", f"净值非正：{net_liquidation}")]
    total = Decimal(0)
    buys = Decimal(0)
    for o in orders:
        if o.symbol not in limits.whitelist:
            out.append(Violation("whitelist", f"订单标的 {o.symbol} 不在白名单"))
        notional = o.notional
        if notional is None:
            out.append(Violation("order_usd", f"{o.client_order_id} 不是限价单，无法确定金额"))
            continue
        if notional > limits.max_order_usd:
            out.append(
                Violation(
                    "order_usd",
                    f"{o.client_order_id} 金额 {notional:,.2f} 超过上限 "
                    f"{limits.max_order_usd:,.2f}",
                )
            )
        total += notional
        if o.side is Side.BUY:
            buys += notional
    if buys > available_cash:
        out.append(
            Violation(
                "cash", f"买入金额 {buys:,.2f} 超过可用资金 {available_cash:,.2f}（不用保证金）"
            )
        )
    turnover = (traded_today + total) / net_liquidation
    if turnover > Decimal(str(limits.max_daily_turnover)):
        out.append(
            Violation(
                "turnover",
                f"当日换手 {turnover:.2f}（已成交 {traded_today:,.2f} + 本批 {total:,.2f}）"
                f" 超过上限 {limits.max_daily_turnover}",
            )
        )
    return out


def check_signal_freshness(
    data_as_of: date, trade_date: date, cal: TradingCalendar
) -> list[Violation]:
    """信号所用数据必须截至执行日的上一个交易日。"""
    expected = cal.previous_trading_day(trade_date)
    if data_as_of != expected:
        return [Violation("stale", f"信号数据截至 {data_as_of}，应为 {expected}")]
    return []


def check_quotes(
    quotes: Mapping[str, Quote], symbols: Collection[str], session_open: datetime
) -> list[Violation]:
    """每个需要交易的标的都要有当日开盘后的报价（延迟行情约 15 分钟）。"""
    out: list[Violation] = []
    for s in sorted(symbols):
        q = quotes.get(s)
        if q is None:
            out.append(Violation("stale", f"{s} 没有报价"))
        elif q.timestamp < session_open:
            out.append(
                Violation("stale", f"{s} 报价时间 {q.timestamp:%Y-%m-%d %H:%M} 早于今日开盘")
            )
    return out
