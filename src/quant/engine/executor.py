"""Executor：目标仓位 → 风控 → 下单 → 跟踪成交（PROJECT_PLAN 6.3、6.4，第 8 章 daily_execute）。

流程（任一步不通过即停止并告警）：
1. 只在常规交易时段、收盘前 stop_before_close_minutes 之前运行。
2. 熔断开关 trading_enabled 为 false 时不下单。
3. 读取上一个交易日的信号；信号数据必须截至上一个交易日，目标仓位通过风控。
4. 恢复：查询券商未完成订单。本策略当日的订单先撤销、同步成交；其他订单视为异常，熔断。
   本地状态未知（已写库但没拿到券商订单号）的订单同样熔断，不盲目重新下单。
5. 对账；不一致时熔断（engine/reconcile.py）。
6. 卖出阶段：按当前持仓与报价计算整批订单（含预估买单）并统一风控，通过后只提交卖单。
7. 买入阶段：卖单全部完成后，按实际现金重新计算买单，再次风控后提交。
8. 限价单：买价 = 参考价 × (1 + limit_offset) 向上取整到分，卖价向下取整；
   超时未成交撤单，按最新报价重挂剩余数量，最多重试 max_retries 次；被拒或重试用尽即停止。

订单先写入 orders 表（拿到券商订单号后补写），订单到终态时写入 fills（每个券商订单一行，
成交价为均价），对账以这些记录为准。

安全：TRADING_MODE=live 在 Phase 5 用户确认前一律拒绝；券商侧的模式与账户检查见 core/safety.py。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import NoReturn
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from quant.broker.base import Broker
from quant.broker.tiger import BrokerError
from quant.core.calendar import TradingCalendar, get_calendar
from quant.core.models import (
    AccountSnapshot,
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
from quant.core.safety import OrderNotAllowedError
from quant.core.settings import Settings
from quant.db.models import Fill, Order, Signal
from quant.engine.orders import OrderPlanParams, PlannedOrder, plan_orders
from quant.engine.reconcile import halt_trading, reconcile, trading_enabled
from quant.notify.base import Level, Notifier
from quant.risk.rules import (
    RiskLimits,
    check_holdings,
    check_orders,
    check_quotes,
    check_signal_freshness,
    check_targets,
    summarize,
)

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
_CENT = Decimal("0.01")
_TICK_SUB_DOLLAR = Decimal("0.0001")
_OPEN_STATES = tuple(s.value for s in OrderState if not s.is_terminal)


class ExecutionError(RuntimeError):
    """执行失败：已撤销本次运行的未完成订单，需要人工查看。"""


class _Halted(Exception):
    """需要熔断并人工处理（已写入 halt_reason）。"""


@dataclass
class ExecutionReport:
    trade_date: date
    strategy: str
    status: str  # done / no_orders / halted / rejected
    message: str = ""
    orders: list[OrderStatus] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{self.trade_date} {self.strategy}：{self.status} {self.message}".strip()]
        for o in self.orders:
            px = f"@ {o.avg_fill_price}" if o.avg_fill_price is not None else ""
            lines.append(f"- {o.side} {o.symbol} {o.filled_qty}/{o.qty} {o.state} {px}".rstrip())
        return "\n".join(lines)


@dataclass
class _Working:
    """一个标的、一个方向的待成交数量；可能跨多次重挂。"""

    symbol: str
    side: Side
    remaining: int
    attempts: int = 0
    order_id: str | None = None
    client_order_id: str = ""
    placed_at: datetime | None = None
    cancel_at: datetime | None = None


def limit_price(ref: Decimal, side: Side, offset: Decimal) -> Decimal:
    """买价上浮、卖价下浮；≥1 美元按分取整（买向上、卖向下），更容易成交。"""
    raw = ref * (1 + offset) if side is Side.BUY else ref * (1 - offset)
    tick = _CENT if raw >= 1 else _TICK_SUB_DOLLAR
    rounding = ROUND_CEILING if side is Side.BUY else ROUND_FLOOR
    px = raw.quantize(tick, rounding=rounding)
    if px <= 0:
        raise ExecutionError(f"限价非正：参考价 {ref}")
    return px


class Executor:
    def __init__(
        self,
        engine: Engine,
        broker: Broker,
        settings: Settings,
        notifier: Notifier,
        strategy: str,
        *,
        calendar: TradingCalendar | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.trading_mode == "live":
            raise OrderNotAllowedError("实盘执行需在 Phase 5 经用户明确确认后开启")
        self._engine = engine
        self._broker = broker
        self._mode = settings.trading_mode
        self._notifier = notifier
        self._strategy = strategy
        self._cal = calendar or get_calendar()
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._limits = RiskLimits.from_settings(settings)
        ex = settings.executor
        self._ex = ex
        self._offset = Decimal(str(ex.limit_offset))
        self._timeout = timedelta(minutes=ex.order_timeout_minutes)
        self._plan_params = OrderPlanParams(
            cash_buffer=Decimal(str(ex.cash_buffer)),
            min_trade_usd=Decimal(str(ex.min_trade_usd)),
            min_weight_diff=Decimal(str(ex.min_weight_diff)),
            # 估算买入成本的余量须覆盖限价上浮，另留 0.3% 给佣金与价格变动
            price_margin=self._offset + Decimal("0.003"),
        )
        self._active: list[_Working] = []
        self._done: list[OrderStatus] = []
        self._trade_date: date | None = None
        self._session_open = datetime.min.replace(tzinfo=UTC)
        self._deadline = datetime.min.replace(tzinfo=UTC)

    # ---- 入口 ----

    def run(self, trade_date: date) -> ExecutionReport:
        """执行一个交易日。熔断、风控拒绝返回报告；执行失败抛出 ExecutionError。"""
        self._trade_date = trade_date
        self._active, self._done = [], []
        try:
            report = self._run(trade_date)
        except _Halted as e:
            report = self._report("halted", str(e))
        except Exception as e:
            self._abort()
            msg = f"{type(e).__name__}: {e}"
            self._notify(f"执行失败 {trade_date}", msg, Level.ERROR)
            if isinstance(e, ExecutionError):
                raise
            raise ExecutionError(msg) from e
        level = Level.INFO if report.status in ("done", "no_orders") else Level.ERROR
        self._notify(f"执行{_STATUS_TEXT[report.status]} {trade_date}", report.summary(), level)
        return report

    def _run(self, d: date) -> ExecutionReport:
        if not self._cal.is_trading_day(d):
            raise ExecutionError(f"{d} 不是交易日")
        self._session_open = self._cal.session_open(d)
        close = self._cal.session_close(d)
        self._deadline = close - timedelta(minutes=self._ex.stop_before_close_minutes)
        now = self._now()
        if not (self._session_open <= now < self._deadline):
            raise ExecutionError(
                f"当前 {now.astimezone(ET):%H:%M} ET 不在可下单时段 "
                f"{self._session_open:%H:%M}–{self._deadline:%H:%M} ET"
            )
        with Session(self._engine) as s:
            if not trading_enabled(s):
                return self._report("halted", "熔断开关关闭（trading_enabled=false），未下单")

        weights, data_as_of = self._load_signal(d)
        violations = check_signal_freshness(data_as_of, d, self._cal)
        violations += check_targets(weights, self._limits)
        if violations:
            return self._report("rejected", summarize(violations))

        self._recover(d)
        with Session(self._engine) as s, s.begin():
            rec = reconcile(s, self._broker, self._mode)
        if not rec.ok:
            return self._report("halted", rec.summary())

        # 卖出阶段：整批（含预估买单）风控
        account, plan = self._plan(weights)
        if not plan:
            return self._report("no_orders", "持仓已接近目标，无需调仓")
        sells = [p for p in plan if p.side is Side.SELL]
        requests = [self._request(p, p.qty, p.ref_price) for p in plan]
        proceeds = sum(
            (r.notional or Decimal(0) for r in requests if r.side is Side.SELL), Decimal(0)
        )
        violations = check_orders(
            requests,
            self._limits,
            net_liquidation=account.net_liquidation,
            available_cash=account.cash + proceeds,
            traded_today=self._traded_today(d),
        )
        if violations:
            return self._report("rejected", summarize(violations))
        self._work(sells)

        # 买入阶段：按实际现金重新计算
        account, plan = self._plan(weights)
        buys = [p for p in plan if p.side is Side.BUY]
        leftover = [p for p in plan if p.side is Side.SELL]
        if leftover:
            log.warning("买入阶段仍有卖单（忽略，次日再调整）：%s", leftover)
        if buys:
            violations = check_orders(
                [self._request(p, p.qty, p.ref_price) for p in buys],
                self._limits,
                net_liquidation=account.net_liquidation,
                available_cash=account.cash,
                traded_today=self._traded_today(d),
            )
            if violations:
                return self._report("rejected", "卖出已完成，买入被拒：" + summarize(violations))
            self._work(buys)
        return self._report("done", f"{len(self._done)} 笔订单")

    # ---- 计算 ----

    def _load_signal(self, d: date) -> tuple[dict[str, float], date]:
        prev = self._cal.previous_trading_day(d)
        with Session(self._engine) as s:
            sig = s.scalar(
                select(Signal).where(Signal.strategy == self._strategy, Signal.signal_date == prev)
            )
            if sig is None:
                raise ExecutionError(f"没有 {self._strategy} 在 {prev} 的信号")
            return dict(sig.target_weights), sig.data_as_of

    def _plan(self, weights: Mapping[str, float]) -> tuple[AccountSnapshot, list[PlannedOrder]]:
        account = self._broker.get_account()
        positions: dict[str, Position] = self._broker.get_positions()
        violations = check_holdings(positions, self._limits)
        symbols = sorted(set(weights) | set(positions))
        quotes = self._broker.get_quotes(symbols)
        violations += check_quotes(quotes, symbols, self._session_open)
        if violations:
            raise ExecutionError(summarize(violations))
        # 参考价：买卖方向未知时统一用最新价，下单时再按方向取买一/卖一
        prices = {s: quotes[s].last for s in symbols}
        plan = plan_orders(
            weights,
            {s: p.qty for s, p in positions.items()},
            prices,
            account.net_liquidation,
            account.cash,
            self._plan_params,
        )
        return account, plan

    def _request(self, p: PlannedOrder | _Working, qty: int, ref: Decimal) -> OrderRequest:
        assert self._trade_date is not None
        attempt = self._next_attempt(p.symbol, p.side)
        cid = make_client_order_id(self._trade_date, self._strategy, p.symbol, p.side, attempt)
        px = limit_price(ref, p.side, self._offset)
        return OrderRequest(cid, p.symbol, p.side, qty, OrderType.LIMIT, px)

    def _next_attempt(self, symbol: str, side: Side) -> int:
        assert self._trade_date is not None
        prefix = make_client_order_id(self._trade_date, self._strategy, symbol, side, 1)[:-1]
        with Session(self._engine) as s:
            ids = s.scalars(
                select(Order.client_order_id).where(Order.client_order_id.startswith(prefix))
            ).all()
        return max((parse_client_order_id(i).attempt for i in ids), default=0) + 1

    def _traded_today(self, d: date) -> Decimal:
        start = datetime.combine(d, dtime(0), ET)
        with Session(self._engine) as s:
            fills = s.scalars(
                select(Fill).where(
                    Fill.mode == self._mode,
                    Fill.filled_at >= start,
                    Fill.filled_at < start + timedelta(days=1),
                )
            ).all()
            return sum((f.price * f.qty for f in fills), Decimal(0))

    # ---- 下单与跟踪 ----

    def _quote(self, symbol: str) -> Quote:
        quote = self._broker.get_quotes([symbol]).get(symbol)
        violations = check_quotes({symbol: quote} if quote else {}, [symbol], self._session_open)
        if quote is None or violations:
            raise ExecutionError(summarize(violations))
        return quote

    def _submit(self, w: _Working) -> None:
        ref = self._quote(w.symbol).reference_price(w.side)
        req = self._request(w, w.remaining, ref)
        violations = [
            v
            for v in check_orders(
                [req],
                self._limits,
                net_liquidation=Decimal(1),
                available_cash=Decimal("Infinity"),
                traded_today=Decimal(0),
            )
            if v.rule in ("whitelist", "order_usd")
        ]
        if violations:
            raise ExecutionError(summarize(violations))
        with Session(self._engine) as s, s.begin():
            s.add(
                Order(
                    client_order_id=req.client_order_id,
                    mode=self._mode,
                    symbol=req.symbol,
                    side=req.side.value,
                    order_type=req.order_type.value,
                    qty=req.qty,
                    limit_price=req.limit_price,
                    reference_price=ref,
                    status=OrderState.SUBMITTED.value,
                )
            )
        try:
            order_id = self._broker.place_order(req)
        except (OrderNotAllowedError, ValueError) as e:
            # 提交前就被拒绝（模式/账户检查、价格精度），券商侧没有订单
            self._set_order(req.client_order_id, status=OrderState.REJECTED.value, reason=str(e))
            raise ExecutionError(f"{req.client_order_id} 未提交：{e}") from e
        except BrokerError as e:
            # 结果未知：券商可能已经收到订单。不重试，熔断等人工核对
            with Session(self._engine) as s, s.begin():
                halt_trading(s, f"订单 {req.client_order_id} 提交结果未知：{e}")
            raise ExecutionError(f"{req.client_order_id} 提交结果未知，已熔断：{e}") from e
        self._set_order(req.client_order_id, broker_order_id=order_id)
        w.order_id, w.client_order_id = order_id, req.client_order_id
        w.placed_at, w.cancel_at = self._now(), None
        w.attempts += 1

    def _work(self, planned: list[PlannedOrder]) -> None:
        """提交并跟踪一批同方向订单，直到全部成交；失败抛出 ExecutionError。"""
        self._active = [_Working(p.symbol, p.side, p.qty) for p in planned]
        for w in self._active:
            self._submit(w)
        while any(w.remaining > 0 for w in self._active):
            self._sleep(self._ex.poll_seconds)
            now = self._now()
            late = now >= self._deadline
            for w in self._active:
                if w.remaining == 0 or w.order_id is None:
                    continue
                st = self._broker.get_order(w.order_id)
                self._sync(st)
                if st.state.is_terminal:
                    w.remaining -= st.filled_qty
                    w.order_id = None
                    self._done.append(st)
                    if st.state is OrderState.REJECTED:
                        raise ExecutionError(f"{st.client_order_id} 被券商拒绝：{st.reason}")
                    if w.remaining == 0:
                        continue
                    if late:
                        raise ExecutionError(
                            f"收盘前 {self._ex.stop_before_close_minutes} 分钟仍未完成："
                            f"{w.side} {w.symbol} 剩余 {w.remaining} 股"
                        )
                    if w.attempts > self._ex.max_retries:
                        raise ExecutionError(
                            f"{w.side} {w.symbol} 重试 {self._ex.max_retries} 次仍未完全成交，"
                            f"剩余 {w.remaining} 股"
                        )
                    self._submit(w)
                elif w.cancel_at is None:
                    assert w.placed_at is not None
                    if late or now - w.placed_at >= self._timeout:
                        self._broker.cancel_order(w.order_id)
                        w.cancel_at = now
                elif now - w.cancel_at >= self._timeout:
                    raise ExecutionError(f"撤单 {w.client_order_id} 超时未确认")
        self._active = []

    def _abort(self) -> None:
        """失败时尽力撤销本次运行的未完成订单；最终状态在下次运行的恢复步骤中同步。"""
        for w in self._active:
            if w.order_id is not None:
                try:
                    self._broker.cancel_order(w.order_id)
                except Exception:
                    log.exception("撤单 %s 失败", w.client_order_id)
        self._active = []

    # ---- 恢复 ----

    def _recover(self, d: date) -> None:
        ours: list[OrderStatus] = []
        for o in self._broker.get_open_orders():
            try:
                key = parse_client_order_id(o.client_order_id or "")
            except ValueError:
                key = None
            if key is None or key.trade_date != d or key.strategy != self._strategy:
                self._halt(f"券商有非本次运行的未完成订单 {o.order_id}（{o.symbol}），需人工处理")
            ours.append(o)
        for o in ours:
            assert o.client_order_id is not None
            with Session(self._engine) as s, s.begin():
                row = s.scalar(select(Order).where(Order.client_order_id == o.client_order_id))
                if row is None:
                    s.add(_order_row(o, self._mode))
                elif row.broker_order_id is None:
                    row.broker_order_id = o.order_id
            log.warning("恢复：撤销当日未完成订单 %s", o.client_order_id)
            self._broker.cancel_order(o.order_id)
        if ours:
            self._wait_terminal([o.order_id for o in ours])

        with Session(self._engine) as s:
            rows = s.scalars(
                select(Order).where(Order.mode == self._mode, Order.status.in_(_OPEN_STATES))
            ).all()
            pending = [(r.client_order_id, r.broker_order_id) for r in rows]
        for cid, oid in pending:
            if oid is None:
                self._halt(f"订单 {cid} 提交结果未知（没有券商订单号），需人工核对")
            st = self._broker.get_order(oid)
            if not st.state.is_terminal:
                self._halt(f"订单 {cid} 仍未完成（{st.state}），需人工处理")
            self._sync(st)

    def _wait_terminal(self, order_ids: list[str]) -> None:
        deadline = self._now() + self._timeout
        while True:
            if all(self._broker.get_order(i).state.is_terminal for i in order_ids):
                return
            if self._now() >= deadline:
                self._halt(f"撤单超时未确认：{order_ids}")
            self._sleep(self._ex.poll_seconds)

    def _halt(self, reason: str) -> NoReturn:
        with Session(self._engine) as s, s.begin():
            halt_trading(s, reason)
        raise _Halted(reason)

    # ---- 持久化 ----

    def _set_order(self, client_order_id: str, **values: object) -> None:
        with Session(self._engine) as s, s.begin():
            row = s.scalar(select(Order).where(Order.client_order_id == client_order_id))
            assert row is not None
            for k, v in values.items():
                setattr(row, k, v)

    def _sync(self, st: OrderStatus) -> None:
        """券商订单状态写入 orders；到终态且有成交时写入 fills（幂等）。"""
        with Session(self._engine) as s, s.begin():
            row = s.scalar(select(Order).where(Order.broker_order_id == st.order_id))
            if row is None:
                row = _order_row(st, self._mode)
                s.add(row)
            row.status = st.state.value
            row.filled_qty = st.filled_qty
            row.avg_fill_price = st.avg_fill_price
            row.reason = st.reason or None
            if not st.state.is_terminal or st.filled_qty == 0:
                return
            if s.scalar(select(Fill.id).where(Fill.broker_order_id == st.order_id)) is not None:
                return
            price = st.avg_fill_price or st.limit_price
            if price is None:
                raise ExecutionError(f"订单 {st.order_id} 已成交但没有成交价")
            if st.commission is None:
                log.warning("订单 %s 没有返回佣金，按 0 记录", st.order_id)
            s.add(
                Fill(
                    broker_order_id=st.order_id,
                    mode=self._mode,
                    symbol=st.symbol,
                    side=st.side.value,
                    qty=st.filled_qty,
                    price=price,
                    commission=st.commission or Decimal(0),
                    filled_at=st.updated_at,
                )
            )

    # ---- 报告 ----

    def _report(self, status: str, message: str) -> ExecutionReport:
        assert self._trade_date is not None
        return ExecutionReport(self._trade_date, self._strategy, status, message, list(self._done))

    def _notify(self, title: str, body: str, level: Level) -> None:
        try:
            self._notifier.send(f"[{self._mode}] {title}", body, level)
        except Exception:
            log.exception("发送通知失败")


_STATUS_TEXT = {
    "done": "完成",
    "no_orders": "完成（无需调仓）",
    "halted": "熔断",
    "rejected": "被风控拒绝",
}


def _order_row(st: OrderStatus, mode: str) -> Order:
    return Order(
        client_order_id=st.client_order_id or f"broker-{st.order_id}",
        broker_order_id=st.order_id,
        mode=mode,
        symbol=st.symbol,
        side=st.side.value,
        order_type=OrderType.LIMIT.value if st.limit_price is not None else OrderType.MARKET.value,
        qty=st.qty,
        limit_price=st.limit_price,
        status=st.state.value,
        filled_qty=st.filled_qty,
        avg_fill_price=st.avg_fill_price,
    )
