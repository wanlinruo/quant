"""swing_pullback 调参与样本外评估（设计第 7 节；计划 Task 7）。在 Mac 上运行，用 yfinance 数据：

    TRADING_MODE=sim uv run python scripts/research/swing_pullback.py [--workers 6]

- 调参区间 2015-01-02 ~ 2022-12-30；样本外 2023-01-03 ~ 最近已收盘交易日（只检验，不调参）。
- 36 组参数全部跑两个区间并记录；按调参区间夏普选最优，再对最优追加 fomc_exit / slope_days 各 1 组。
- 基准：持有 QQQ、持有 SPY、8 只个股等权月末再平衡；成本为老虎固定式套餐 + 滑点。
- 财报反应日需先 `quant events sync-earnings` 生成；FOMC 决议日为包内数据。
- 输出 docs/research/2026-10-29-swing-pullback.md。
"""

from __future__ import annotations

import argparse
import itertools
import time
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.core.settings import load_settings
from quant.data.events import load_earnings, load_fomc_dates
from quant.data.pipeline import ensure_bars
from quant.data.providers.yfinance import YFinanceProvider
from quant.data.store import ParquetStore
from quant.engine.backtester import Backtester
from quant.engine.evaluation import meets_criteria
from quant.engine.metrics import Metrics, buy_and_hold, compute_metrics, equal_weight_rebalanced
from quant.engine.orders import OrderPlanParams
from quant.engine.trades import round_trips, trade_stats
from quant.strategies.events import EventCalendar
from quant.strategies.swing_pullback import SwingParams, SwingPullback

REPORT = Path(__file__).resolve().parents[2] / "docs" / "research" / "2026-10-29-swing-pullback.md"
DATA_START = date(2013, 1, 2)  # 留出 120 日线 + 250 日重放的预热
IS_START, IS_END = date(2015, 1, 2), date(2022, 12, 30)
OOS_START = date(2023, 1, 3)
CASH = Decimal("100000")
CLIFF = 0.7  # 相邻参数调参区间夏普低于最优的 70% 视为断崖

KEYS = ("trend_ma", "support_ma", "confirm", "exit_ma")
CHOICES: dict[str, tuple[Any, ...]] = {
    "trend_ma": (120, 50),
    "support_ma": (20, 30, 50),
    "confirm": ("close_strength", "volume", "rsi2"),
    "exit_ma": (5, 10),
}
GRID: list[dict[str, Any]] = [
    dict(zip(KEYS, combo, strict=True)) for combo in itertools.product(*CHOICES.values())
]


def neighbors(best: Mapping[str, Any]) -> list[dict[str, Any]]:
    """GRID 中与 best 恰有一个维度不同的组合。"""
    return [g for g in GRID if sum(g[k] != best[k] for k in KEYS) == 1]


def select_best(results: pd.DataFrame) -> dict[str, Any]:
    """调参区间夏普最高；并列时最大回撤较小者。"""
    ranked = results.sort_values(["is_sharpe", "is_max_drawdown"], ascending=[False, False])
    row = ranked.iloc[0]
    return {k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in KEYS}


# ---- 回测（子进程共用的数据在 initializer 中准备一次）----

_CTX: dict[str, Any] = {}


def _init(bars: Mapping[str, pd.DataFrame], events: EventCalendar) -> None:
    settings = load_settings()
    ex = settings.executor
    _CTX.update(
        bars=bars,
        events=events,
        costs=CostModel.from_settings(settings.costs),
        plan=OrderPlanParams(
            cash_buffer=Decimal(str(ex.cash_buffer)),
            min_trade_usd=Decimal(str(ex.min_trade_usd)),
            min_weight_diff=Decimal(str(ex.min_weight_diff)),
        ),
        cal=get_calendar(),
    )


def _run(params: dict[str, Any], start: date, end: date) -> dict[str, Any]:
    bt = Backtester(
        SwingPullback(),
        _CTX["bars"],
        _CTX["cal"],
        _CTX["costs"],
        CASH,
        _CTX["plan"],
        params,
        benchmark=None,
        events=_CTX["events"],
    )
    res = bt.run(start, end)
    stats = trade_stats(round_trips(res.fills))
    return {"metrics": res.metrics, "stats": stats, "equity": res.equity}


def _job(args: tuple[dict[str, Any], date]) -> dict[str, Any]:
    params, oos_end = args
    t0 = time.time()
    ins = _run(params, IS_START, IS_END)
    oos = _run(params, OOS_START, oos_end)
    row: dict[str, Any] = dict(params)
    for prefix, r in (("is", ins), ("oos", oos)):
        m: Metrics = r["metrics"]
        row |= {f"{prefix}_{k}": getattr(m, k) for k in ("cagr", "max_drawdown", "sharpe", "calmar",
                                                          "volatility", "turnover")}  # fmt: skip
        row |= {f"{prefix}_{k}": v for k, v in r["stats"].items()}
    row["_metrics"] = (ins["metrics"], oos["metrics"])
    row["seconds"] = round(time.time() - t0, 1)
    return row


# ---- 基准 ----


def benchmarks(
    bars: Mapping[str, pd.DataFrame], stocks: list[str], start: date, end: date
) -> dict[str, Metrics]:
    def px(s: str) -> pd.Series:
        a = bars[s]["adj_close"]
        return a[(a.index >= pd.Timestamp(start)) & (a.index <= pd.Timestamp(end))]

    wide = pd.DataFrame({s: px(s) for s in stocks}).dropna()
    return {
        "QQQ": compute_metrics(buy_and_hold(px("QQQ"), float(CASH))),
        "SPY": compute_metrics(buy_and_hold(px("SPY"), float(CASH))),
        "等权 8 只": compute_metrics(equal_weight_rebalanced(wide, float(CASH))),
    }


# ---- 报告 ----


def _pct(x: float) -> str:
    return "—" if x != x else f"{x:.1%}"


def _num(x: float) -> str:
    return "—" if x != x else f"{x:.2f}"


def _label(p: Mapping[str, Any] | pd.Series) -> str:
    return f"{p['trend_ma']}/{p['support_ma']}/{p['confirm']}/{p['exit_ma']}"


def _mrow(name: str, m: Metrics) -> str:
    return (f"| {name} | {_pct(m.cagr)} | {_pct(m.max_drawdown)} | {_num(m.sharpe)} | "
            f"{_num(m.calmar)} | {_pct(m.volatility)} |")  # fmt: skip


def write_report(
    results: pd.DataFrame,
    best: dict[str, Any],
    extras: list[dict[str, Any]],
    bench_is: dict[str, Metrics],
    bench_oos: dict[str, Metrics],
    oos_end: date,
    yearly: pd.Series,
    full_stats: dict[str, float],
    events_note: str,
) -> None:
    b = results[(results[list(KEYS)] == pd.Series(best)).all(axis=1)].iloc[0]
    m_is, m_oos = b["_metrics"]
    c_is = meets_criteria(m_is, bench_is["QQQ"], bench_is["等权 8 只"])
    c_oos = meets_criteria(m_oos, bench_oos["QQQ"], bench_oos["等权 8 只"])
    nb = results[results.apply(lambda r: sum(r[k] != best[k] for k in KEYS) == 1, axis=1)]
    cliff = nb[nb["is_sharpe"] < CLIFF * b["is_sharpe"]]
    passed = c_is.passed and c_oos.passed and cliff.empty

    def yes(x: bool) -> str:
        return "✅" if x else "❌"

    lines = [
        "# swing_pullback 调参与样本外评估",
        "",
        f"生成时间：{datetime.now(UTC):%Y-%m-%d %H:%M} UTC。设计：`docs/superpowers/specs/"
        "2026-10-29-swing-pullback-design.md`；脚本：`scripts/research/swing_pullback.py`。",
        "",
        f"调参区间 {IS_START} ~ {IS_END}；样本外 {OOS_START} ~ {oos_end}（只检验）。",
        f"初始资金 {CASH}；成本为老虎固定式套餐 + 滑点；{events_note}",
        "",
        f"## 结论：{'通过' if passed else '未通过'}",
        "",
        f"最优参数（调参区间夏普最高）：`{_label(best)}`"
        "（大方向均线 / 支撑均线 / 止跌确认 / 离场均线）。",
        "",
        "| 判定项 | 调参区间 | 样本外 |",
        "|---|---|---|",
        f"| 夏普 > QQQ | {yes(c_is.sharpe_vs_qqq)} | {yes(c_oos.sharpe_vs_qqq)} |",
        f"| 夏普 ≥ 等权 8 只 | {yes(c_is.sharpe_vs_equal)} | {yes(c_oos.sharpe_vs_equal)} |",
        f"| 最大回撤 ≤ QQQ × 0.6 | {yes(c_is.drawdown)} | {yes(c_oos.drawdown)} |",
        f"| 年化 ≥ QQQ × 0.8 | {yes(c_is.cagr)} | {yes(c_oos.cagr)} |",
        f"| 相邻参数无断崖（调参区间夏普 ≥ 最优 × {CLIFF}） | {yes(cliff.empty)} | — |",
        "",
        "## 最优参数 vs 基准",
        "",
        "| 区间 / 对象 | 年化 | 最大回撤 | 夏普 | 卡玛 | 波动 |",
        "|---|---|---|---|---|---|",
        _mrow("调参 · 策略", m_is),
        *[_mrow(f"调参 · {k}", v) for k, v in bench_is.items()],
        _mrow("样本外 · 策略", m_oos),
        *[_mrow(f"样本外 · {k}", v) for k, v in bench_oos.items()],
        "",
        f"交易统计（{IS_START} ~ {oos_end} 全区间）：{int(full_stats['count'])} 笔，胜率 "
        f"{_pct(full_stats['win_rate'])}，盈亏比 {_num(full_stats['payoff'])}，平均持有 "
        f"{_num(full_stats['avg_holding_days'])} 个交易日。",
        "",
        "逐年收益：",
        "",
        "| 年份 | 收益 |",
        "|---|---|",
        *[f"| {y} | {_pct(r)} |" for y, r in yearly.items()],
        "",
        "## 相邻参数（稳健性）",
        "",
        "| 参数 | 调参夏普 | 调参回撤 | 样本外夏普 | 样本外回撤 | 样本外年化 |",
        "|---|---|---|---|---|---|",
        *[
            f"| {_label(r)} | {_num(r['is_sharpe'])} | {_pct(r['is_max_drawdown'])} | "
            f"{_num(r['oos_sharpe'])} | {_pct(r['oos_max_drawdown'])} | {_pct(r['oos_cagr'])} |"
            for _, r in nb.iterrows()
        ],
        "",
        "## 追加组合（基于最优参数）",
        "",
        "| 变化 | 调参夏普 | 调参回撤 | 样本外夏普 | 样本外回撤 | 样本外年化 |",
        "|---|---|---|---|---|---|",
        *[
            f"| {e['_change']} | {_num(e['is_sharpe'])} | {_pct(e['is_max_drawdown'])} | "
            f"{_num(e['oos_sharpe'])} | {_pct(e['oos_max_drawdown'])} | {_pct(e['oos_cagr'])} |"
            for e in extras
        ],
        "",
        "## 全部 36 组（按调参区间夏普排序）",
        "",
        "| 参数 | 调参年化 | 调参回撤 | 调参夏普 | 样本外年化 | 样本外回撤 | 样本外夏普 | "
        "交易笔数（调参） | 胜率（调参） | 平均持有天数（调参） |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *[
            f"| {_label(r)} | {_pct(r['is_cagr'])} | {_pct(r['is_max_drawdown'])} | "
            f"{_num(r['is_sharpe'])} | {_pct(r['oos_cagr'])} | {_pct(r['oos_max_drawdown'])} | "
            f"{_num(r['oos_sharpe'])} | {int(r['is_count'])} | {_pct(r['is_win_rate'])} | "
            f"{_num(r['is_avg_holding_days'])} |"
            for _, r in results.sort_values("is_sharpe", ascending=False).iterrows()
        ],
        "",
        "## 已知局限",
        "",
        "- 幸存者偏差：七姐妹是事后选出的赢家，所以加入“等权 8 只”基准；仍可能高估未来表现。",
        "- “大单托举”只能用日线特征近似（放量、收在高位、短期超卖后回升）。",
        "- 财报反应日来自 EDGAR 8-K Item 2.02 受理时间；TSLA 季度交付数据也计为事件。",
        "- 回测在次日开盘成交；实盘为 09:50 ET 按延迟行情限价单执行，存在执行偏差。",
        "- 样本外约 3.8 年，覆盖的市场环境有限。",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    settings = load_settings()
    cal = get_calendar()
    oos_end = cal.last_completed_trading_day(datetime.now(UTC))
    p = SwingParams()
    symbols = sorted({*p.symbols, "SPY"})
    bars = ensure_bars(
        ParquetStore(settings.data_dir), YFinanceProvider(), cal, symbols, DATA_START, oos_end,
        max_move=SwingPullback.max_daily_move,
    )  # fmt: skip
    earnings_path = settings.data_dir / "events" / "earnings.csv"
    events = EventCalendar(earnings=load_earnings(earnings_path), fomc=load_fomc_dates())
    events_note = (
        f"财报反应日 {sum(len(v) for v in events.earnings.values())} 条（EDGAR），"
        f"FOMC 决议日 {len(events.fomc)} 条。"
    )
    stocks = list(p.stocks)
    bench_is = benchmarks(bars, stocks, IS_START, IS_END)
    bench_oos = benchmarks(bars, stocks, OOS_START, oos_end)

    t0 = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(bars, events)) as pool:
        rows = list(pool.map(_job, [(g, oos_end) for g in GRID]))
        results = pd.DataFrame(rows)
        best = select_best(results)
        variants = [("fomc_exit=True", {**best, "fomc_exit": True}),
                    ("slope_days=10", {**best, "slope_days": 10})]  # fmt: skip
        extras = list(pool.map(_job, [(v, oos_end) for _, v in variants]))
        for (label, _), e in zip(variants, extras, strict=True):
            e["_change"] = label
        full = pool.submit(_full, best, oos_end).result()
    print(f"共 {len(GRID) + len(extras)} 组，耗时 {time.time() - t0:.0f} 秒；最优 {_label(best)}")
    write_report(results, best, extras, bench_is, bench_oos, oos_end, full["yearly"],
                 full["stats"], events_note)  # fmt: skip
    print(f"报告：{REPORT}")
    return 0


def _full(params: dict[str, Any], end: date) -> dict[str, Any]:
    r = _run(params, IS_START, end)
    eq: pd.Series = r["equity"]
    yearly = eq.groupby(pd.DatetimeIndex(eq.index).year).agg(lambda s: s.iloc[-1])
    yearly = yearly.pct_change().fillna(yearly.iloc[0] / float(CASH) - 1)
    return {"yearly": yearly, "stats": r["stats"]}


if __name__ == "__main__":
    raise SystemExit(main())
