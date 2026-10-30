"""每日任务（PROJECT_PLAN 第 8 章）。时间一律为美东时间，交易日以 XNYS 日历为准。

- daily_data（收盘后）：从主数据源（data.primary）拉取策略所需区间的日线（含分红复权价每天
  整体刷新，分红后历史 adj_close 会变化）→ 写入 Parquet → 校验；当日数据未出时等待重试；
  配置了 data.secondary 时抽样比对收盘价。服务器上为老虎 + 新浪（#41）。
  策略需要候选池时（组合策略，#47）另外同步：标普 500 成分名单、调仓日的候选池日线（新浪 + 老虎
  拆股记录）；需要财报日时同步老虎财报日历。
- daily_signal：当日数据任务成功后运行策略，写入 signals，推送“下一交易日计划”。
- daily_execute（下一交易日开盘后）：Executor（engine/executor.py）。
- daily_report（执行后）：对账 → 一致则写入当日快照 → 推送日报。

sim 模式不连接券商：只运行 daily_data 与 daily_signal。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant.broker.base import Broker
from quant.core.calendar import TradingCalendar, get_calendar
from quant.core.settings import Settings
from quant.data.events import live_earnings_path, load_events, sync_live_earnings
from quant.data.pipeline import DataValidationError, ensure_bars
from quant.data.providers.base import DataProvider
from quant.data.store import ParquetStore
from quant.data.universe import (
    constituents_path,
    load_constituents,
    stale_warning,
    sync_constituents,
)
from quant.data.universe_bars import sync_universe_bars, universe_store
from quant.data.validate import Report, compare_sources
from quant.db.models import Fill, Signal
from quant.engine.backtester import store_field_loader
from quant.engine.executor import ET, ExecutionError, Executor
from quant.engine.reconcile import reconcile, record_snapshot, trading_enabled
from quant.engine.runner import BENCHMARK
from quant.jobs.runner import JobStopped, job_status
from quant.notify.base import Level, Notifier
from quant.strategies import get_strategy
from quant.strategies.base import Context
from quant.strategies.momentum_select import rebalance_day

log = logging.getLogger("quant.jobs")

JOBS = ("daily_data", "daily_signal", "daily_execute", "daily_report")
DATA_RETRIES = 3
DATA_RETRY_SECONDS = 15 * 60
COMPARE_DAYS = 45
UNIVERSE_HISTORY_DAYS = 450  # 动量排序要约 252 个交易日 + 预热


@dataclass
class JobContext:
    settings: Settings
    engine: Engine
    notifier: Notifier
    store: ParquetStore
    provider: DataProvider
    # 抽样比对数据源（老虎）；sim 模式为 None
    secondary: DataProvider | None = None
    # 创建券商连接；sim 模式为 None
    broker_factory: Callable[[], Broker] | None = None
    # 候选池数据源（data.universe）；未配置为 None
    universe_provider: DataProvider | None = None
    calendar: TradingCalendar = field(default_factory=get_calendar)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_settings(cls, settings: Settings, engine: Engine, notifier: Notifier) -> JobContext:
        factory: Callable[[], Broker] | None = None
        if settings.trading_mode in ("paper", "live"):
            from quant.broker.tiger import TigerBroker

            def factory() -> Broker:
                return TigerBroker.from_settings(settings)

        primary = make_provider(settings, settings.data.primary)
        assert primary is not None
        return cls(
            settings,
            engine,
            notifier,
            ParquetStore(settings.data_dir),
            primary,
            make_provider(settings, settings.data.secondary),
            factory,
            make_provider(settings, settings.data.universe),
        )

    @property
    def mode(self) -> str:
        return self.settings.trading_mode

    @property
    def strategy_name(self) -> str:
        return self.settings.strategy.name


def make_provider(settings: Settings, name: str) -> DataProvider | None:
    if name == "yfinance":
        from quant.data.providers.yfinance import YFinanceProvider

        return YFinanceProvider()
    if name == "tiger":
        from quant.data.providers.tiger import TigerProvider

        return TigerProvider.from_settings(settings, adjust_dividends=True)
    if name == "sina":
        from quant.data.providers.sina import SinaProvider

        return SinaProvider()
    return None


def _symbols(ctx: JobContext) -> list[str]:
    strategy = get_strategy(ctx.strategy_name)
    return sorted({*strategy.symbols(ctx.settings.strategy.params), BENCHMARK})


def _data_start(ctx: JobContext, d: date) -> date:
    strategy = get_strategy(ctx.strategy_name)
    warmup = strategy.warmup_days(ctx.settings.strategy.params)
    return d - timedelta(days=int(warmup * 1.5) + 10)


def daily_data(ctx: JobContext, d: date) -> str:
    symbols = _symbols(ctx)
    start = _data_start(ctx, d)
    for attempt in range(DATA_RETRIES + 1):
        try:
            ensure_bars(
                ctx.store,
                ctx.provider,
                ctx.calendar,
                symbols,
                start,
                d,
                refresh=True,
                max_move=get_strategy(ctx.strategy_name).max_daily_move,
            )
            break
        except DataValidationError as e:
            # 当日数据未出：只有“最新数据过期”与“缺少当日”两类问题
            not_ready = all(
                i.kind == "stale" or (i.kind == "missing_day" and i.day == d)
                for i in e.report.issues
            )
            if not not_ready or attempt == DATA_RETRIES:
                raise
            log.warning(
                "job=daily_data trade_date=%s 当日数据未出，%d 秒后重试", d, DATA_RETRY_SECONDS
            )
            ctx.sleep(DATA_RETRY_SECONDS)
    extras, warnings = sync_strategy_extras(ctx, d)
    if warnings:
        ctx.notifier.send(f"[{ctx.mode}] 数据提示 {d}", "\n".join(warnings), Level.WARNING)
    if ctx.secondary is not None:
        report = Report()
        for s in symbols:
            other = ctx.secondary.daily_bars(s, d - timedelta(days=COMPARE_DAYS), d)
            report.extend(compare_sources(s, ctx.store.read_bars(s, start, d), other))
        if not report.ok:
            raise DataValidationError(report)
    return "；".join([f"{len(symbols)} 个标的截至 {d}", *extras])


def sync_strategy_extras(ctx: JobContext, d: date) -> tuple[list[str], list[str]]:
    """策略声明需要的额外数据：成分名单 + 候选池日线、实盘财报日。返回（摘要, 需要提示的问题）。"""
    strategy = get_strategy(ctx.strategy_name)
    params = ctx.settings.strategy.params
    data_dir = ctx.settings.data_dir
    summary: list[str] = []
    warnings: list[str] = []
    if strategy.uses_universe:
        if ctx.universe_provider is None:
            raise RuntimeError(f"策略 {ctx.strategy_name} 需要候选池数据，但 data.universe 未配置")
        splits = None
        if ctx.settings.data.universe == "sina":
            splits = getattr(ctx.provider, "splits", None)
            if splits is None:
                raise RuntimeError("data.universe=sina 需要主数据源提供拆股记录（老虎）")
        universe = sync_constituents(constituents_path(data_dir), d)
        if (stale := stale_warning(universe, d)) is not None:
            warnings.append(stale)
        me = rebalance_day(ctx.calendar, d)
        members = universe.members(me)
        r = sync_universe_bars(
            universe_store(data_dir), ctx.universe_provider, splits, ctx.calendar,
            members, me - timedelta(days=UNIVERSE_HISTORY_DAYS), me,
        )  # fmt: skip
        summary.append(
            f"候选池 {me}：{len(members)} 只，覆盖率 {r.coverage(len(members)):.1%}，"
            f"新拉取 {len(r.fetched)}"
        )
        if r.missing:
            warnings.append(f"候选池拿不到 {len(r.missing)} 只：{', '.join(r.missing[:20])}")
        for sym, why in list(r.excluded.items())[:20]:
            warnings.append(f"候选池排除 {sym}：{why}")
    symbols = strategy.earnings_symbols(params)
    calendar_fn = getattr(ctx.provider, "earnings_calendar", None)
    if symbols and calendar_fn is not None:
        got = sync_live_earnings(
            live_earnings_path(data_dir),
            lambda a, b: calendar_fn(symbols, a, b, ctx.calendar),
            d,
        )
        summary.append(f"财报日 {sum(1 for s in symbols if s in got)}/{len(symbols)} 只")
    return summary, warnings


def daily_signal(ctx: JobContext, d: date) -> str:
    with Session(ctx.engine) as s:
        if job_status(s, "daily_data", d) != "success":
            raise RuntimeError(f"{d} 的 daily_data 未成功，不生成信号")
    strategy = get_strategy(ctx.strategy_name)
    params = ctx.settings.strategy.params
    for sym in strategy.symbols(params):
        bars = ctx.store.read_bars(sym, d - timedelta(days=10), d)
        last = bars.index[-1].date() if len(bars) else None
        if last != d:
            raise RuntimeError(f"{sym} 最新数据为 {last}，不是 {d}")
    weights = strategy.run(build_context(ctx, d))
    with Session(ctx.engine) as s, s.begin():
        prev = s.scalar(
            select(Signal.target_weights)
            .where(Signal.strategy == ctx.strategy_name, Signal.signal_date < d)
            .order_by(Signal.signal_date.desc())
            .limit(1)
        )
        stmt = insert(Signal).values(
            strategy=ctx.strategy_name, signal_date=d, target_weights=weights, data_as_of=d
        )
        s.execute(
            stmt.on_conflict_do_update(
                index_elements=[Signal.strategy, Signal.signal_date],
                set_={"target_weights": weights, "data_as_of": d},
            )
        )
    nxt = ctx.calendar.next_trading_day(d)
    text = _weights_text(weights)
    changed = "（与上一信号相同）" if prev == weights else "（调仓）"
    ctx.notifier.send(
        f"[{ctx.mode}] 信号 {d}",
        f"目标仓位：{text} {changed}\n执行：{nxt} {ctx.settings.schedule.daily_execute} ET",
        Level.INFO,
    )
    return text


def build_context(ctx: JobContext, d: date) -> Context:
    """信号日 d 的策略 Context：本地数据（主目录优先、其次候选池目录）、事件日历、成分名单。"""
    strategy = get_strategy(ctx.strategy_name)
    data_dir = ctx.settings.data_dir
    return Context(
        as_of=d,
        loader=ctx.store.prices,
        params=ctx.settings.strategy.params,
        field_loader=store_field_loader([ctx.store, universe_store(data_dir)]),
        events=load_events(data_dir),
        universe=load_constituents(constituents_path(data_dir)) if strategy.uses_universe else None,
    )


def daily_execute(ctx: JobContext, d: date) -> str:
    if ctx.broker_factory is None:
        return "sim 模式不连接券商，跳过"
    executor = Executor(
        ctx.engine,
        ctx.broker_factory(),
        ctx.settings,
        ctx.notifier,
        ctx.strategy_name,
        calendar=ctx.calendar,
        now=ctx.now,
        sleep=ctx.sleep,
    )
    report = executor.run(d)  # 失败时抛出 ExecutionError（已通知）
    if report.status not in ("done", "no_orders"):
        raise JobStopped(report.summary())
    return report.status


def daily_report(ctx: JobContext, d: date) -> str:
    if ctx.broker_factory is None:
        return "sim 模式不连接券商，跳过"
    broker = ctx.broker_factory()
    with Session(ctx.engine) as s, s.begin():
        rec = reconcile(s, broker, ctx.mode)
        account = broker.get_account()
        positions = broker.get_positions()
        if rec.ok:
            record_snapshot(s, ctx.mode, d, account, positions)
    with Session(ctx.engine) as s:
        enabled = trading_enabled(s)
        start = datetime.combine(d, datetime.min.time(), ET)
        fills = s.scalars(
            select(Fill)
            .where(Fill.mode == ctx.mode, Fill.filled_at >= start)
            .where(Fill.filled_at < start + timedelta(days=1))
            .order_by(Fill.filled_at)
        ).all()
        fill_lines = [f"- {f.side} {f.symbol} {f.qty} @ {f.price:.2f}" for f in fills]
    nlv = account.net_liquidation
    pos_lines = [
        f"- {p.symbol} {p.qty} 股，{p.market_value:,.2f}（{p.market_value / nlv:.1%}）"
        for p in sorted(positions.values(), key=lambda p: p.symbol)
    ]
    body = "\n".join(
        [
            f"净值 {nlv:,.2f}，现金 {account.cash:,.2f}（{account.cash / nlv:.1%}）",
            "持仓：" if pos_lines else "持仓：无",
            *pos_lines,
            f"当日成交 {len(fill_lines)} 笔" + ("：" if fill_lines else ""),
            *fill_lines,
            rec.summary(),
            f"熔断开关：{'开' if enabled else '关（不下单）'}",
        ]
    )
    level = Level.INFO if rec.ok else Level.ERROR
    ctx.notifier.send(f"[{ctx.mode}] 日报 {d}", body, level)
    if not rec.ok:
        raise JobStopped(rec.summary())
    return f"净值 {nlv:,.2f}"


def _weights_text(weights: dict[str, float]) -> str:
    if not weights:
        return "空仓"
    return "，".join(f"{s} {w:.0%}" for s, w in sorted(weights.items(), key=lambda x: -x[1]))


JOB_FUNCS: dict[str, Callable[[JobContext, date], str]] = {
    "daily_data": daily_data,
    "daily_signal": daily_signal,
    "daily_execute": daily_execute,
    "daily_report": daily_report,
}

# 这些异常在抛出前已经通知过
QUIET: tuple[type[BaseException], ...] = (JobStopped, ExecutionError)
