"""命令行入口。

    uv run quant backtest --strategy etf_momentum --start 2016-10-28 --end 2026-10-28 \\
        --commission-per-share 0.005 --min-commission 1 --param lookback=126

数据：先读 Parquet（settings.data_dir），不覆盖回测区间时从 yfinance 拉取并校验，校验失败即终止。
结果：打印指标；配置了数据库（QUANT_DATABASE_URL）时写入 backtest_runs / backtest_equity。
佣金：老虎实际费率待确认（issue #5），未在配置中填写时必须用参数显式给出。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from quant.core.calendar import get_calendar
from quant.core.settings import ConfigError, Settings, load_settings
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider
from quant.engine.backtester import BacktestResult
from quant.engine.metrics import Metrics
from quant.engine.runner import (
    BENCHMARK,
    BacktestSpec,
    costs_with_overrides,
    run_spec,
    save_result,
)
from quant.strategies import STRATEGIES


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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "backtest":
            run_backtest(args, settings)
    except (ConfigError, DataValidationError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
