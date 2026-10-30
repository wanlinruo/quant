"""回测引擎（PROJECT_PLAN 第 9 章）。

每个交易日 d：
1. 开盘：按 d 的开盘价撮合前一日提交的卖单；随后按实际现金和开盘价重新计算剩余订单
   （主要是买单）并在同一开盘价成交——即“先卖后买，卖单成交后再按实际现金计算买单”。
2. 收盘：按 d 的收盘价估值，记录净值与持仓权重。
3. 调用策略（Context.as_of = d，只能看到 d 及之前的数据）得到目标权重；
   用 plan_orders（与实盘 Executor 共用）计算订单，只提交卖单，买单留到下一交易日开盘计算。

价格口径：撮合与估值使用按 adj_close / close 比例调整后的开高低收（含分红复权），
使回测收益包含分红；股数因此与历史实际略有差异，不影响收益率。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

import pandas as pd

from quant.broker.sim import CostModel, SimBroker
from quant.core.calendar import TradingCalendar
from quant.core.models import Bar, Fill, OrderRequest, OrderType, Side, make_client_order_id
from quant.data.store import ParquetStore
from quant.engine.metrics import Metrics, buy_and_hold, compute_metrics, drawdown
from quant.engine.orders import OrderPlanParams, PlannedOrder, plan_orders
from quant.strategies.base import FIELDS, Context, FieldLoader, Strategy
from quant.strategies.events import EventCalendar
from quant.strategies.universe import Constituents

_PRICE_STEP = Decimal("0.0001")


def adjusted_ohlc(bars: pd.DataFrame) -> pd.DataFrame:
    """把开高低收按 adj_close / close 调整为含分红复权口径。"""
    if bars["adj_close"].isna().any():
        raise ValueError("回测需要完整的 adj_close（含分红复权价）")
    factor = bars["adj_close"] / bars["close"]
    out = bars.copy()
    for c in ("open", "high", "low"):
        out[c] = bars[c] * factor
    out["close"] = bars["adj_close"]
    return out


def field_loader_from_bars(bars: Mapping[str, pd.DataFrame]) -> FieldLoader:
    """由各标的日线构造 FieldLoader：价格按 adjusted_ohlc 复权，成交量不调整。纯函数。"""
    adj = {s: adjusted_ohlc(df) for s, df in bars.items()}
    wide = {f: pd.DataFrame({s: df[f] for s, df in adj.items()}).sort_index() for f in FIELDS}

    def load(symbols: Sequence[str], name: str, start: date | None, end: date) -> pd.DataFrame:
        df = wide[name]
        cols = [s for s in symbols if s in df.columns]
        out = df.loc[: pd.Timestamp(end), cols]
        if start is not None:
            out = out.loc[pd.Timestamp(start) :]
        out = out.reindex(columns=list(symbols))
        out.index.name = "date"
        return out

    return load


def store_field_loader(stores: Sequence[ParquetStore]) -> FieldLoader:
    """由本地 Parquet 构造 FieldLoader（实盘每日任务用）：每个代码取第一个有数据的 store，
    价格按 adjusted_ohlc 复权；所有 store 都没有的代码为整列 NaN。"""

    def load(symbols: Sequence[str], name: str, start: date | None, end: date) -> pd.DataFrame:
        cols: dict[str, pd.Series] = {}
        for s in symbols:
            for store in stores:
                bars = store.read_bars(s, start, end)
                if len(bars):
                    cols[s] = adjusted_ohlc(bars)[name]
                    break
        out = pd.DataFrame(cols).sort_index().reindex(columns=list(symbols))
        out.index.name = "date"
        return out

    return load


def _dec(x: float) -> Decimal:
    return Decimal(repr(float(x))).quantize(_PRICE_STEP)


@dataclass
class BacktestResult:
    equity: pd.Series
    drawdown: pd.Series
    weights: pd.DataFrame  # 每日收盘后的实际持仓权重
    fills: list[Fill]
    metrics: Metrics
    benchmark: Metrics | None
    benchmark_equity: pd.Series | None = None
    signals: dict[date, dict[str, float]] = field(default_factory=dict)


class Backtester:
    def __init__(
        self,
        strategy: Strategy,
        bars: Mapping[str, pd.DataFrame],
        calendar: TradingCalendar,
        costs: CostModel,
        initial_cash: Decimal = Decimal("100000"),
        plan_params: OrderPlanParams | None = None,
        params: Mapping[str, object] | None = None,
        benchmark: str | None = "SPY",
        events: EventCalendar | None = None,
        universe: Constituents | None = None,
    ) -> None:
        self.strategy = strategy
        self.calendar = calendar
        self.costs = costs
        self.initial_cash = initial_cash
        self.plan_params = plan_params or OrderPlanParams()
        if strategy.min_weight_diff is not None:
            self.plan_params = replace(
                self.plan_params, min_weight_diff=Decimal(str(strategy.min_weight_diff))
            )
        self.events = events or EventCalendar()
        self.universe = universe
        self._fields = field_loader_from_bars(bars)
        self.params = dict(params or {})
        self.benchmark = benchmark
        self._adj = {s: adjusted_ohlc(df) for s, df in bars.items()}
        self._wide = pd.DataFrame({s: df["close"] for s, df in self._adj.items()}).sort_index()
        self._wide.index.name = "date"
        # 日期 → (开, 高, 低, 收, 量)，避免每天逐行索引 DataFrame
        cols = ["open", "high", "low", "close", "volume"]
        self._rows: dict[str, dict[date, tuple[float, ...]]] = {
            s: {
                ts.date(): tuple(float(v) for v in row)
                for ts, row in zip(
                    pd.DatetimeIndex(df.index), df[cols].to_numpy(dtype=float), strict=True
                )
            }
            for s, df in self._adj.items()
        }

    def _loader(self, symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
        cols = [s for s in symbols if s in self._wide.columns]
        df = self._wide.loc[: pd.Timestamp(end), cols]
        if start is not None:
            df = df.loc[pd.Timestamp(start) :]
        return df.reindex(columns=list(symbols))

    def _bars_on(self, day: date) -> dict[str, Bar]:
        out = {}
        for s, rows in self._rows.items():
            if (row := rows.get(day)) is not None:
                o, h, lo, c = (_dec(v) for v in row[:4])
                out[s] = Bar(s, day, o, max(h, o, c), min(lo, o, c), c, int(row[4]))
        return out

    def _submit(self, broker: SimBroker, signal_day: date, orders: list[PlannedOrder]) -> None:
        for o in orders:
            cid = make_client_order_id(signal_day, self.strategy.name, o.symbol, o.side, 1)
            broker.place_order(OrderRequest(cid, o.symbol, o.side, o.qty, OrderType.MARKET))

    def _rebalance_at_open(
        self, broker: SimBroker, bars: dict[str, Bar], signal_day: date, target: dict[str, float]
    ) -> list[Fill]:
        """卖单已按开盘价成交后，用实际现金与开盘价重新计算订单并按开盘价撮合。"""
        positions = broker.get_positions()
        prices = {
            s: q.last for s, q in broker.get_quotes(sorted(set(target) | set(positions))).items()
        }
        prices.update({s: b.open for s, b in bars.items()})
        cash = broker.get_account().cash
        nav = cash + sum((prices[s] * p.qty for s, p in positions.items()), start=Decimal(0))
        qty = {s: p.qty for s, p in positions.items()}
        orders = plan_orders(target, qty, prices, nav, cash, self.plan_params)
        self._submit(broker, signal_day, orders)
        return broker.match_open()

    def run(self, start: date, end: date) -> BacktestResult:
        days = self.calendar.trading_days(start, end)
        if len(days) < 2:
            raise ValueError("回测区间至少需要 2 个交易日")
        broker = SimBroker(self.initial_cash, self.costs, days[0])
        equity: dict[date, Decimal] = {}
        weights: dict[date, dict[str, float]] = {}
        signals: dict[date, dict[str, float]] = {}
        traded = Decimal(0)
        pending: tuple[date, dict[str, float]] | None = None  # (信号日, 目标权重)

        for day in days:
            bars = self._bars_on(day)
            fills = broker.open_session(day, bars)
            if pending is not None:
                signal_day, target = pending
                fills += self._rebalance_at_open(broker, bars, signal_day, target)
                pending = None
            broker.close_session()
            traded += sum((f.gross for f in fills), start=Decimal(0))
            acct = broker.get_account()
            positions = broker.get_positions()
            equity[day] = acct.net_liquidation
            weights[day] = {
                s: float(p.market_value / acct.net_liquidation) for s, p in positions.items()
            }
            if day == days[-1]:
                break  # 最后一天提交的订单不会成交
            ctx = Context(
                as_of=day,
                loader=self._loader,
                params=self.params,
                field_loader=self._fields,
                events=self.events,
                universe=self.universe,
            )
            target = self.strategy.run(ctx)
            signals[day] = target
            quotes = broker.get_quotes(sorted(set(target) | set(positions)))
            prices = {s: q.last for s, q in quotes.items()}
            orders = plan_orders(
                target,
                {s: p.qty for s, p in positions.items()},
                prices,
                acct.net_liquidation,
                acct.cash,
                self.plan_params,
            )
            if orders:
                self._submit(broker, day, [o for o in orders if o.side is Side.SELL])
                pending = (day, target)

        eq = pd.Series({pd.Timestamp(d): float(v) for d, v in equity.items()}, name="equity")
        eq.index.name = "date"
        metrics = compute_metrics(eq, float(traded), len(broker.fills))
        bench_eq: pd.Series | None = None
        bench: Metrics | None = None
        if self.benchmark and self.benchmark in self._wide.columns:
            px = self._wide[self.benchmark].loc[eq.index[0] : eq.index[-1]]
            bench_eq = buy_and_hold(px, float(self.initial_cash))
            bench = compute_metrics(bench_eq)
        w = pd.DataFrame.from_dict(weights, orient="index").fillna(0.0)
        w.index = pd.DatetimeIndex(w.index, name="date")
        return BacktestResult(
            equity=eq,
            drawdown=drawdown(eq),
            weights=w,
            fills=list(broker.fills),
            metrics=metrics,
            benchmark=bench,
            benchmark_equity=bench_eq,
            signals=signals,
        )
