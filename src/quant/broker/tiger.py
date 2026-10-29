"""TigerBroker：封装 tigeropen（PROJECT_PLAN 6.1）。模拟账户与实盘账户只是配置不同。

安全（必须遵守）：
- 构造时与每次下单前都调用 check_order_allowed：paper 只允许模拟账户，live 只允许实盘账户
  且不能在开发机上运行，sim 模式拒绝一切下单。
- 账户号只以脱敏形式出现在日志、异常信息中；SDK 异常信息中的账户号会被替换。
- tigeropen 类型只出现在本模块，对外只返回 quant.core.models 中的领域模型。

行为约定（依据官方文档 https://docs.itigerup.com/docs/place-order ）：
- 只在常规交易时段下单：显式设置 outside_rth=False（SDK 默认允许盘前盘后）。
- 限价单当日有效（DAY）；美股价格 ≥ 1 美元时限价精度为 0.01，否则拒绝提交。
- client_order_id 写入 user_mark（查询时返回）；对应关系仍以本地数据库 orders 表为准。
- 报价默认用免费延迟行情（issue #3 方案 3），没有买卖价；可配置为实时行情。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from quant.broker.base import Broker
from quant.core.models import (
    AccountSnapshot,
    OrderRequest,
    OrderState,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
)
from quant.core.safety import check_order_allowed, is_paper_account, mask_account
from quant.core.settings import Settings, TradingMode

log = logging.getLogger(__name__)

QuoteSource = Literal["delayed", "realtime"]

# 券商状态 → 领域状态；PENDING_CANCEL 仍视为未完成
_STATE = {
    "PENDING_NEW": OrderState.SUBMITTED,
    "NEW": OrderState.SUBMITTED,
    "HELD": OrderState.SUBMITTED,
    "PENDING_CANCEL": OrderState.SUBMITTED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELLED": OrderState.CANCELLED,
    "REJECTED": OrderState.REJECTED,
    "EXPIRED": OrderState.EXPIRED,
}
_CENT = Decimal("0.01")


class BrokerError(RuntimeError):
    """券商接口调用失败（信息已脱敏）。"""


def _dec(x: Any) -> Decimal:
    return Decimal(str(x))


def _ms_to_dt(ms: Any) -> datetime:
    if ms in (None, "", 0):
        return datetime.now(UTC)
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _status_name(status: Any) -> str:
    return str(getattr(status, "name", status))


def make_clients(settings: Settings) -> tuple[Any, Any, str]:
    """按配置创建 (TradeClient, QuoteClient, account)；配置文件必须为 600 权限。"""
    from tigeropen.quote.quote_client import QuoteClient
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient

    props_dir = settings.tiger_props_file().parent
    cfg = TigerOpenClientConfig(props_path=str(props_dir) + "/")
    return TradeClient(cfg), QuoteClient(cfg), str(cfg.account)


class TigerBroker(Broker):
    def __init__(
        self,
        trading_mode: TradingMode,
        account: str,
        trade_client: Any,
        quote_client: Any,
        quote_source: QuoteSource = "delayed",
    ) -> None:
        # 构造时就检查一次：模式与账户类型不匹配直接拒绝
        check_order_allowed(trading_mode, "tiger", account)
        self._mode = trading_mode
        self._account = account
        self._trade = trade_client
        self._quote = quote_client
        self._quote_source = quote_source
        log.info(
            "TigerBroker 就绪：mode=%s account=%s（%s）quotes=%s",
            trading_mode,
            mask_account(account),
            "模拟" if is_paper_account(account) else "实盘",
            quote_source,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, quote_source: QuoteSource = "delayed"
    ) -> TigerBroker:
        trade, quote, account = make_clients(settings)
        return cls(settings.trading_mode, account, trade, quote, quote_source)

    @property
    def masked_account(self) -> str:
        return mask_account(self._account)

    def _call(self, what: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}".replace(self._account, self.masked_account)
            raise BrokerError(f"老虎 {what} 失败：{msg}") from None

    # ---- 账户与持仓 ----

    def get_account(self) -> AccountSnapshot:
        assets = self._call("查询资产", self._trade.get_prime_assets, base_currency="USD")
        seg = assets.segments["S"]
        return AccountSnapshot(
            cash=_dec(seg.cash_balance),
            net_liquidation=_dec(seg.net_liquidation),
            buying_power=_dec(seg.buying_power),
            timestamp=datetime.now(UTC),
            currency=str(seg.currency or "USD"),
        )

    def get_positions(self) -> dict[str, Position]:
        from tigeropen.common.consts import SecurityType

        raw = self._call("查询持仓", self._trade.get_positions, sec_type=SecurityType.STK) or []
        out: dict[str, Position] = {}
        for p in raw:
            qty = int(p.position_qty if p.position_qty is not None else p.quantity)
            if qty == 0:
                continue
            if qty < 0:
                raise BrokerError(
                    f"{p.contract.symbol} 出现空头持仓 {qty}，本系统不做空，请人工检查"
                )
            out[p.contract.symbol] = Position(
                symbol=p.contract.symbol,
                qty=qty,
                avg_cost=_dec(p.average_cost),
                market_value=_dec(p.market_value),
            )
        return out

    # ---- 报价 ----

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        if self._quote_source == "realtime":
            df = self._call("查询实时行情", self._quote.get_stock_briefs, symbols)
            out = {}
            for _, r in df.iterrows():
                bid, ask = r.get("bid_price"), r.get("ask_price")
                out[r["symbol"]] = Quote(
                    symbol=r["symbol"],
                    last=_dec(r["latest_price"]),
                    timestamp=_ms_to_dt(r.get("latest_time")),
                    bid=_dec(bid) if bid and bid > 0 else None,
                    ask=_dec(ask) if ask and ask > 0 else None,
                )
            return out
        df = self._call("查询延迟行情", self._quote.get_stock_delay_briefs, symbols)
        return {
            r["symbol"]: Quote(
                symbol=r["symbol"],
                last=_dec(r["close"]),
                timestamp=_ms_to_dt(r.get("time")),
                delayed=True,
            )
            for _, r in df.iterrows()
            if r.get("close") and r["close"] > 0 and not r.get("halted")
        }

    # ---- 订单 ----

    def place_order(self, req: OrderRequest) -> str:
        from tigeropen.common.util.contract_utils import stock_contract
        from tigeropen.common.util.order_utils import limit_order, market_order

        # 每次下单前再次检查交易模式与账户类型
        check_order_allowed(self._mode, "tiger", self._account)
        contract = stock_contract(symbol=req.symbol, currency="USD")
        action = "BUY" if req.side is Side.BUY else "SELL"
        if req.order_type is OrderType.LIMIT:
            assert req.limit_price is not None
            if req.limit_price >= 1 and req.limit_price != req.limit_price.quantize(_CENT):
                raise ValueError(f"限价 {req.limit_price} 精度超过 0.01，老虎会拒单")
            order = limit_order(
                account=self._account,
                contract=contract,
                action=action,
                quantity=req.qty,
                limit_price=float(req.limit_price),
                time_in_force="DAY",
            )
        else:
            order = market_order(
                account=self._account,
                contract=contract,
                action=action,
                quantity=req.qty,
                time_in_force="DAY",
            )
        order.outside_rth = False  # 只在常规时段成交
        order.user_mark = req.client_order_id
        order_id = self._call("下单", self._trade.place_order, order)
        if not order_id:
            raise BrokerError(f"老虎下单未返回订单号：{req.client_order_id}")
        log.info(
            "已提交订单 %s：%s %s %s %s @ %s（account %s）",
            order_id,
            req.client_order_id,
            action,
            req.qty,
            req.symbol,
            req.limit_price if req.limit_price is not None else "MKT",
            self.masked_account,
        )
        return str(order_id)

    def cancel_order(self, order_id: str) -> None:
        self._call("撤单", self._trade.cancel_order, id=int(order_id))
        log.info("已请求撤单 %s", order_id)

    def _to_status(self, o: Any) -> OrderStatus:
        name = _status_name(o.status)
        state = _STATE.get(name)
        if state is None:
            raise BrokerError(f"未知订单状态 {name}（订单 {o.id}）")
        qty = int(o.quantity)
        filled = int(o.filled or 0)
        avg = _dec(o.avg_fill_price) if o.avg_fill_price else None
        limit = _dec(o.limit_price) if o.limit_price else None
        updated = getattr(o, "update_time", None) or o.trade_time or o.order_time
        fee = getattr(o, "commission", None)
        fee_dec = _dec(fee) if fee not in (None, "") else None
        return OrderStatus(
            order_id=str(o.id),
            client_order_id=getattr(o, "user_mark", None) or None,
            symbol=o.contract.symbol,
            side=Side.BUY if str(o.action).upper() == "BUY" else Side.SELL,
            qty=qty,
            filled_qty=filled,
            state=state,
            updated_at=_ms_to_dt(updated),
            limit_price=limit,
            avg_fill_price=avg if avg and avg > 0 else None,
            reason=str(o.reason or ""),
            commission=abs(fee_dec) if fee_dec is not None and fee_dec.is_finite() else None,
        )

    def get_order(self, order_id: str) -> OrderStatus:
        o = self._call("查询订单", self._trade.get_order, id=int(order_id))
        if o is None:
            raise BrokerError(f"订单不存在：{order_id}")
        return self._to_status(o)

    def get_open_orders(self) -> list[OrderStatus]:
        from tigeropen.common.consts import Market, SecurityType

        raw = self._call(
            "查询未完成订单",
            self._trade.get_open_orders,
            sec_type=SecurityType.STK,
            market=Market.US,
        )
        return [self._to_status(o) for o in raw or []]
