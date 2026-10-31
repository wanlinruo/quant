"""小资金实盘可行性测算（#55）：组合策略在 3500 / 10000 美元本金下，整股与碎股的回测差异。

- 成本：configs/base.yaml 的老虎固定式套餐 + 滑点；执行参数取 executor 配置。
- 整股：直接用真实本金回测。
- 碎股：回测引擎只支持整股，用等价放大法：本金、每笔最低费用（platform_min / min_commission）、
  最小成交额同时 ×SCALE；按股计费的部分随股数自然放大，封顶比例与滑点不受规模影响。
  放大后整股取整误差可忽略，结果等价于真实本金下可买碎股。假设碎股与整股同一套费率。
- 数据：复用 scripts/research/combo.py 的本地缓存（yfinance、成分名单、财报日），不联网。

用法：uv run python scripts/research/small_capital.py [--end 2026-10-28]
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import combo as research

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.core.settings import load_settings
from quant.data.universe import constituents_path, load_constituents
from quant.engine.backtester import Backtester
from quant.engine.metrics import Metrics
from quant.engine.orders import OrderPlanParams
from quant.strategies.combo import Combo

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "research" / "2026-10-31-small-capital.md"
SCALE = Decimal(1000)
# (名称, 本金, 碎股)
SCENARIOS: list[tuple[str, Decimal, bool]] = [
    ("10 万美元（研究基准）", Decimal(100000), False),
    ("1 万美元 · 整股", Decimal(10000), False),
    ("1 万美元 · 碎股", Decimal(10000), True),
    ("3500 美元 · 整股", Decimal(3500), False),
    ("3500 美元 · 碎股", Decimal(3500), True),
]


def scaled(
    costs: CostModel, plan: OrderPlanParams, k: Decimal
) -> tuple[CostModel, OrderPlanParams]:
    """碎股等价放大：按笔的固定金额 ×k，按股、按比例的不变。"""
    fixed = {"min_commission": costs.min_commission * k, "platform_min": costs.platform_min * k}
    return replace(costs, **fixed), replace(plan, min_trade_usd=plan.min_trade_usd * k)


_CTX: dict[str, Any] = {}


def _init(bars: Any, events: Any, universe: Any) -> None:
    settings = load_settings()
    ex = settings.executor
    _CTX.update(
        bars=bars, events=events, universe=universe, cal=get_calendar(),
        costs=CostModel.from_settings(settings.costs),
        plan=OrderPlanParams(
            cash_buffer=Decimal(str(ex.cash_buffer)),
            min_trade_usd=Decimal(str(ex.min_trade_usd)),
            min_weight_diff=Decimal(str(ex.min_weight_diff)),
        ),
    )  # fmt: skip


def _run(args: tuple[str, Decimal, bool, date]) -> dict[str, Any]:
    name, cash, fractional, end = args
    t0 = time.time()
    c = _CTX
    costs, plan, k = c["costs"], c["plan"], Decimal(1)
    if fractional:
        k = SCALE
        costs, plan = scaled(costs, plan, k)
    bt = Backtester(Combo(c["cal"]), c["bars"], c["cal"], costs, cash * k, plan, {},
                    benchmark=None, events=c["events"], universe=c["universe"])  # fmt: skip
    res = bt.run(research.START, end)
    fees = float(sum((f.commission for f in res.fills), start=Decimal(0)) / k)
    eq = res.equity / float(k)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    print(f"  {name} 完成，{time.time() - t0:.0f} 秒", flush=True)
    return {
        "name": name, "cash": float(cash), "equity": eq, "metrics": res.metrics,
        "fees": fees, "fills": len(res.fills), "fees_per_year": fees / years,
    }  # fmt: skip


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_report(runs: list[dict[str, Any]], end: date) -> None:
    base = runs[0]["metrics"]
    lines = [
        "# 小资金实盘可行性测算：整股与碎股",
        "",
        f"生成时间：{datetime.now(UTC):%Y-%m-%d %H:%M} UTC。"
        "脚本：`scripts/research/small_capital.py`；issue #55。",
        "",
        f"区间 {research.START} ~ {end}；组合策略基准配置"
        "（与 `docs/research/2026-10-30-combo.md` 相同）；"
        "成本为老虎固定式套餐（佣金 0.0039/股 + 平台费 0.004/股、每笔最低 1 美元，"
        "各自封顶成交额 0.5%）+ 滑点 0.05%；次日开盘成交。"
        "碎股按等价放大法模拟（本金、每笔最低费用、最小成交额 ×1000），"
        "假设碎股与整股费率相同。",
        "",
        "| 场景 | 年化 | 最大回撤 | 夏普 | 年化差（对 10 万） | 成交笔数 | 年均费用 "
        "| 年均费用 / 本金 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        m: Metrics = r["metrics"]
        lines.append(
            f"| {r['name']} | {_pct(m.cagr)} | {_pct(m.max_drawdown)} | {m.sharpe:.2f} | "
            f"{(m.cagr - base.cagr) * 100:+.1f} 个百分点 | {r['fills']} | "
            f"{r['fees_per_year']:.0f} 美元 | {_pct(r['fees_per_year'] / r['cash'])} |"
        )
    lines += ["", "## 逐年收益", "", "| 年份 | " + " | ".join(r["name"] for r in runs) + " |",
              "|---|" + "---|" * len(runs)]  # fmt: skip
    yearly = [research._yearly(r["equity"]) for r in runs]
    for y in yearly[0].index:
        cells = " | ".join(_pct(float(s.get(y, float("nan")))) for s in yearly)
        lines.append(f"| {y} | {cells} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 28))
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    settings = load_settings()
    path = constituents_path(settings.data_dir)
    if not path.exists():
        raise SystemExit(f"缺少成分名单 {path}：先运行 scripts/research/combo.py")
    universe = load_constituents(path)
    combo = Combo(get_calendar())
    fixed = set(combo.symbols({})) | {"SPY"}
    wanted = universe.all_symbols(research.UNIVERSE_START, args.end) | fixed
    print(f"准备数据：{len(wanted)} 只（本地缓存）", flush=True)
    bars, _, _ = research.load_all_bars(wanted, args.end)
    if not (settings.data_dir / "events" / "earnings_sp500.csv").exists():
        raise SystemExit("缺少财报缓存：先运行 scripts/research/combo.py")
    picked = research.picked_symbols(bars, universe, args.end)
    events, _ = research.load_events(picked, user_agent="", refresh=False)
    jobs = [(n, c, f, args.end) for n, c, f in SCENARIOS]
    with ProcessPoolExecutor(args.workers, initializer=_init,
                             initargs=(bars, events, universe)) as pool:  # fmt: skip
        runs = list(pool.map(_run, jobs))
    write_report(runs, args.end)
    print(f"报告：{REPORT}")
    for r in runs:
        m = r["metrics"]
        print(f"{r['name']}: 年化 {_pct(m.cagr)} 回撤 {_pct(m.max_drawdown)} 夏普 {m.sharpe:.2f} "
              f"年均费用 {r['fees_per_year']:.0f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
