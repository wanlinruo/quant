"""命令行入口。

    uv run quant backtest --strategy etf_momentum --start 2016-10-28 --end 2026-10-28 \\
        --commission-per-share 0.005 --min-commission 1 --param lookback=126

数据：先读 Parquet（settings.data_dir），不覆盖回测区间时从 yfinance 拉取并校验，校验失败即终止。
结果：打印指标；配置了数据库（QUANT_DATABASE_URL）时写入 backtest_runs / backtest_equity。
佣金：默认取配置（老虎固定式套餐，#5）；--commission-per-share / --min-commission 只覆盖佣金部分。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from quant.core.calendar import get_calendar
from quant.core.settings import ConfigError, Settings, load_settings
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider, DataUnavailableError
from quant.engine.backtester import BacktestResult
from quant.engine.metrics import Metrics
from quant.engine.runner import (
    BENCHMARK,
    BacktestSpec,
    costs_with_overrides,
    run_spec,
    save_result,
)
from quant.jobs.daily import JOBS
from quant.strategies import STRATEGIES, get_strategy


def _parse_param(text: str) -> tuple[str, Any]:
    key, sep, raw = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"参数格式应为 key=value：{text!r}")
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw.split(",") if "," in raw else raw
    return key, value


def _decimal(text: str) -> Decimal:
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"不是数字：{text!r}") from None
    if not d.is_finite() or d < 0:
        raise argparse.ArgumentTypeError(f"必须为非负数：{text!r}")
    return d


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant", description="个人美股量化交易系统")
    sub = parser.add_subparsers(dest="command", required=True)
    bt = sub.add_parser("backtest", help="运行回测")
    bt.add_argument("--strategy", default="etf_momentum", choices=sorted(STRATEGIES))
    bt.add_argument("--start", type=date.fromisoformat, required=True)
    bt.add_argument("--end", type=date.fromisoformat, help="默认最近一个已收盘交易日")
    bt.add_argument("--cash", type=_decimal, default=Decimal("100000"))
    bt.add_argument("--param", type=_parse_param, action="append", default=[], metavar="K=V")
    bt.add_argument("--commission-per-share", type=_decimal)
    bt.add_argument("--min-commission", type=_decimal)
    bt.add_argument("--slippage", type=_decimal)
    bt.add_argument("--refresh-data", action="store_true", help="忽略本地数据，重新拉取")
    bt.add_argument("--no-db", action="store_true", help="不写入数据库")
    sub.add_parser("notify-test", help="发送一条测试通知（检查 QUANT_NOTIFY_WEBHOOK_URL 配置）")

    job = sub.add_parser("job", help="手动运行一个每日任务（遵守 job_runs 幂等）")
    job.add_argument("name", choices=JOBS)
    job.add_argument("--date", type=date.fromisoformat, help="交易日，默认今天（美东）")
    job.add_argument("--rerun-failed", action="store_true", help="重跑已失败的任务（先确认原因）")

    base = sub.add_parser("baseline", help="把券商当前持仓记为对账基准（首次启用或人工核对后）")
    base.add_argument("--confirm", action="store_true", help="已有快照时覆盖当日快照")

    ev = sub.add_parser("events", help="事件数据（财报反应日）")
    ev_sub = ev.add_subparsers(dest="events_command", required=True)
    sync = ev_sub.add_parser("sync-earnings", help="从 SEC EDGAR 同步历史财报反应日（研究用）")
    sync.add_argument(
        "--user-agent", required=True, help='SEC 要求的联系方式，如 "名称 邮箱"（不会写入仓库）'
    )

    pv = sub.add_parser("preview", help="预览策略的目标仓位（不下单、不写数据库）")
    pv.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    pv.add_argument("--date", type=date.fromisoformat, help="信号日，默认最近一个已收盘交易日")
    pv.add_argument("--sync", action="store_true", help="先按 daily_data 同步数据（不写 job_runs）")
    pv.add_argument("--universe", choices=["sina", "yfinance"], help="覆盖 data.universe")
    pv.add_argument("--param", type=_parse_param, action="append", default=[], metavar="K=V")

    tr = sub.add_parser("trading", help="查看或人工切换熔断开关 trading_enabled")
    tr.add_argument("action", choices=["status", "enable", "disable"])
    tr.add_argument("--reason", default="人工关闭")
    tr.add_argument("--yes", action="store_true", help="确认开启交易")
    return parser


def _pct(x: float) -> str:
    return f"{x:.2%}"


def _num(x: float) -> str:
    return f"{x:.2f}"


def _fmt(m: Metrics) -> dict[str, str]:
    pct, num = _pct, _num
    return {
        "区间": f"{m.start} ~ {m.end}",
        "总收益": pct(m.total_return),
        "年化收益": pct(m.cagr),
        "年化波动": pct(m.volatility),
        "最大回撤": pct(m.max_drawdown),
        "Sharpe": num(m.sharpe),
        "Calmar": num(m.calmar),
        "年化换手": num(m.turnover),
        "交易次数": str(m.trades),
    }


def print_report(result: BacktestResult, out: Any = sys.stdout) -> None:
    rows = _fmt(result.metrics)
    bench = _fmt(result.benchmark) if result.benchmark else {}
    print(f"{'指标':<10}{'策略':>24}{f'基准 {BENCHMARK}':>24}", file=out)
    for k, v in rows.items():
        b = bench.get(k, "-") if k != "交易次数" else "-"
        print(f"{k:<10}{v:>24}{b:>24}", file=out)
    w = result.weights
    if len(w):
        held = w.gt(0).mean().sort_values(ascending=False)
        share = ", ".join(f"{s} {v:.0%}" for s, v in held.items() if v > 0)
        print(f"持仓天数占比：{share}", file=out)


def run_backtest(
    args: argparse.Namespace,
    settings: Settings,
    provider: DataProvider | None = None,
    out: Any = sys.stdout,
) -> BacktestResult:
    end = args.end or get_calendar().last_completed_trading_day(datetime.now(UTC))
    costs = costs_with_overrides(
        settings, args.commission_per_share, args.min_commission, args.slippage
    )
    spec = BacktestSpec(args.strategy, args.start, end, costs, args.cash, dict(args.param))
    result = run_spec(spec, settings, provider, args.refresh_data)
    print_report(result, out)

    if args.no_db or settings.database_url is None:
        print("未写入数据库（--no-db 或未配置 QUANT_DATABASE_URL）", file=out)
        return result
    from sqlalchemy.orm import Session

    from quant.db.backtests import create_run, make_engine

    engine = make_engine(settings.database_url.get_secret_value())
    with Session(engine) as session, session.begin():
        run_id = create_run(
            session, spec.strategy, spec.stored_params(), spec.start, spec.end, "running"
        )
        save_result(session, run_id, result)
    engine.dispose()
    print(f"已写入数据库：backtest_runs.id = {run_id}", file=out)
    return result


def notify_test(settings: Settings, out: Any = None) -> None:
    from quant.notify.base import Level
    from quant.notify.wecom import make_notifier

    notifier = make_notifier(settings)
    notifier.send(
        f"[{settings.trading_mode}] 测试通知",
        "收到这条消息说明告警配置正确。\n时间：" + f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        Level.INFO,
    )
    print(f"已通过 {type(notifier).__name__} 发送测试通知（失败时见日志）", file=out or sys.stdout)


def _engine(settings: Settings) -> Any:
    if settings.database_url is None:
        raise ConfigError("未配置 QUANT_DATABASE_URL")
    from quant.db.backtests import make_engine

    return make_engine(settings.database_url.get_secret_value())


def run_job_command(args: argparse.Namespace, settings: Settings, out: Any = None) -> int:
    from quant.engine.executor import ET
    from quant.jobs.daily import JOB_FUNCS, QUIET, JobContext
    from quant.jobs.runner import run_job
    from quant.notify.wecom import make_notifier

    engine = _engine(settings)
    ctx = JobContext.from_settings(settings, engine, make_notifier(settings))
    day = args.date or datetime.now(UTC).astimezone(ET).date()
    fn = JOB_FUNCS[args.name]
    result = run_job(
        engine,
        args.name,
        day,
        lambda: fn(ctx, day),
        ctx.notifier,
        rerun_failed=args.rerun_failed,
        quiet=QUIET,
    )
    print(f"{args.name} {day}：{result}", file=out or sys.stdout)
    engine.dispose()
    return 0 if result in ("success", "skipped") else 1


class _PrintNotifier:
    def __init__(self, out: Any) -> None:
        self.out = out

    def send(self, title: str, body: str, level: Any) -> None:
        print(f"[{level}] {title}\n{body}", file=self.out)


def preview(
    args: argparse.Namespace,
    settings: Settings,
    out: Any = None,
    ctx_factory: Callable[[Settings, Any], Any] | None = None,
) -> int:
    """按 daily_signal 的方式算出目标仓位并打印；不连接券商、不写数据库、不发通知。"""
    from quant.core.settings import StrategySettings
    from quant.engine.executor import ET
    from quant.jobs.daily import JobContext, _weights_text, build_context, daily_data
    from quant.strategies.combo import Combo, ComboParams
    from quant.strategies.momentum_select import MomentumParams, MomentumSelect, rebalance_day

    out = out or sys.stdout
    if args.param:
        params: dict[str, Any] = dict(args.param)
    elif args.strategy == settings.strategy.name:
        params = dict(settings.strategy.params)
    else:
        params = {}
    update: dict[str, Any] = {"strategy": StrategySettings(name=args.strategy, params=params)}
    if args.universe:
        update["data"] = settings.data.model_copy(update={"universe": args.universe})
    cfg = settings.model_copy(update=update)
    notifier = _PrintNotifier(out)

    def default_factory(s: Settings, n: Any) -> JobContext:
        return JobContext.from_settings(s, None, n)  # type: ignore[arg-type]  # 不使用数据库

    ctx = (ctx_factory or default_factory)(cfg, notifier)
    cal = get_calendar()
    d = args.date or cal.last_completed_trading_day(datetime.now(UTC).astimezone(ET))
    if args.sync:
        print(f"数据：{daily_data(ctx, d)}", file=out)
    context = build_context(ctx, d)
    strategy = get_strategy(args.strategy)
    weights = strategy.run(context)
    print(f"信号日 {d}，策略 {args.strategy}", file=out)
    print(f"目标仓位：{_weights_text(weights)}", file=out)
    momentum: MomentumSelect | None = None
    if isinstance(strategy, Combo):
        p = ComboParams.from_mapping(params)
        factor = strategy.timing_factor(context, p)
        print(f"择时系数：{factor}（{p.vol_symbol} 近 {p.vol_window} 日波动率阈值 "
              f"{p.vol_threshold:.0%}）", file=out)  # fmt: skip
        momentum, mparams = strategy.momentum, p.momentum_params()
    elif isinstance(strategy, MomentumSelect):
        momentum, mparams = strategy, params
    if momentum is not None and context.universe is not None:
        me = rebalance_day(cal, d)
        n = len(context.universe.members(me))
        ranked = momentum.ranking(context, me, MomentumParams.from_mapping(mparams))
        print(f"动量排名（{me}，{n} 只成分股）前 10：{', '.join(ranked[:10])}", file=out)
    return 0


def baseline(args: argparse.Namespace, settings: Settings, out: Any = None) -> int:
    """首次启用模拟盘（或人工核对后）把券商当前持仓写为当日快照，作为对账起点。"""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from quant.broker.tiger import TigerBroker
    from quant.db.models import AccountSnapshotRow, PositionSnapshot
    from quant.engine.executor import ET
    from quant.engine.reconcile import record_snapshot

    out = out or sys.stdout
    if settings.broker != "tiger":
        raise ConfigError("baseline 只用于 paper / live（需要券商持仓）")
    engine = _engine(settings)
    broker = TigerBroker.from_settings(settings)
    if broker.get_open_orders():
        print("券商有未完成订单，等其完成或撤销后再记录基准", file=out)
        return 1
    # 基准代表上一个收盘日结束时的持仓：快照日当天及之前的成交视为已包含在快照中，
    # 所以不能记为今天，否则今天执行产生的成交不会计入对账
    now = datetime.now(UTC)
    cal = get_calendar()
    today = now.astimezone(ET).date()
    if cal.is_trading_day(today) and cal.session_open(today) <= now < cal.session_close(today):
        print("交易时段内不能记录基准，请在收盘后或开盘前执行", file=out)
        return 1
    day = cal.last_completed_trading_day(now)
    with Session(engine) as s, s.begin():
        last = s.scalar(
            select(func.max(AccountSnapshotRow.snapshot_date)).where(
                AccountSnapshotRow.mode == settings.trading_mode
            )
        )
        if last is not None and not args.confirm:
            print(f"已有 {last} 的快照；确认要以券商当前持仓为准时加 --confirm", file=out)
            return 1
        # 覆盖时删除更晚的快照，否则对账仍以更晚的快照为准
        for table in (AccountSnapshotRow, PositionSnapshot):
            s.query(table).filter(
                table.mode == settings.trading_mode, table.snapshot_date > day
            ).delete()
        account, positions = broker.get_account(), broker.get_positions()
        record_snapshot(s, settings.trading_mode, day, account, positions)
    held = ", ".join(f"{p.symbol} {p.qty}" for p in positions.values()) or "无持仓"
    print(f"已记录 {day} 基准（账户 {broker.masked_account}）：{held}", file=out)
    engine.dispose()
    return 0


def trading(args: argparse.Namespace, settings: Settings, out: Any = None) -> int:
    from sqlalchemy.orm import Session

    from quant.db.models import SystemFlag
    from quant.engine.reconcile import HALT_REASON, enable_trading, halt_trading, trading_enabled

    out = out or sys.stdout
    engine = _engine(settings)
    with Session(engine) as s, s.begin():
        reason = s.get(SystemFlag, HALT_REASON)
        if args.action == "disable":
            halt_trading(s, args.reason)
        elif args.action == "enable":
            if not args.yes:
                print("开启交易需加 --yes；请先确认对账一致、熔断原因已处理", file=out)
                return 1
            enable_trading(s)
        enabled = trading_enabled(s)
        print(f"trading_enabled={'true' if enabled else 'false'}", file=out)
        if reason is not None:
            print(f"上次熔断原因：{reason.value}", file=out)
    engine.dispose()
    return 0


def sync_earnings(args: argparse.Namespace, settings: Settings, out: Any = None) -> int:
    from quant.data import events
    from quant.data.providers import edgar

    out = out or sys.stdout
    data = {}
    for sym in sorted(edgar.CIKS):
        data[sym] = edgar.fetch_earnings(sym, args.user_agent)
        last = data[sym][-1] if data[sym] else "无"
        print(f"{sym}: {len(data[sym])} 次，最近 {last}", file=out)
    path = settings.data_dir / "events" / "earnings.csv"
    events.save_earnings(path, data)
    print(f"已写入 {path}", file=out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
        if args.command == "backtest":
            run_backtest(args, settings)
        elif args.command == "notify-test":
            notify_test(settings)
        elif args.command == "job":
            return run_job_command(args, settings)
        elif args.command == "baseline":
            return baseline(args, settings)
        elif args.command == "trading":
            return trading(args, settings)
        elif args.command == "preview":
            try:
                return preview(args, settings)
            except (DataUnavailableError, RuntimeError, OSError) as e:
                # 人工排查用：给一行错误即可（数据源不可用、缺配置、网络等），不打印堆栈
                print(f"错误：{type(e).__name__}: {e}", file=sys.stderr)
                return 1
        elif args.command == "events":
            return sync_earnings(args, settings)
    except (ConfigError, DataValidationError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
