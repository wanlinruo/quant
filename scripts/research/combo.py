"""组合策略 combo 的正式回测与判定（设计 specs/2026-10-30-momentum-combo-design.md 第 7 节；#47）。

在 Mac 上运行，用 yfinance 数据与 fja05680/sp500 的标普 500 历史成分名单：

    NO_PROXY=data.sec.gov,www.sec.gov TRADING_MODE=sim uv run python scripts/research/combo.py \
        --user-agent "<SEC 要求的联系方式>" [--constituents PATH] [--end YYYY-MM-DD] [--workers 6]

- 区间 2015-01-02 ~ 最近已收盘交易日；调参区间 2015–2022、样本外 2023 至今分别报告（样本外在快速
  试验中已看过，真正的样本外是之后的模拟盘）。
- 10 组配置（CONFIGS）全部用回测引擎跑（含成本）；按“组合（持有过财报）”与 6 组相邻设置判定。
- SEC User-Agent 只经命令行传入，不写入报告和日志。
- 输出 docs/research/2026-10-30-combo.md。
"""

from __future__ import annotations

import argparse
import pickle
import time
import urllib.request
from collections.abc import Collection, Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.core.settings import load_settings
from quant.data.events import load_earnings, load_fomc_dates, save_earnings
from quant.data.pipeline import DataValidationError, ensure_bars
from quant.data.providers import edgar
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.yfinance import YFinanceProvider
from quant.data.store import ParquetStore
from quant.data.universe import load_constituents
from quant.engine.backtester import Backtester, field_loader_from_bars
from quant.engine.evaluation import combo_checks
from quant.engine.metrics import Metrics, buy_and_hold, compute_metrics
from quant.engine.orders import OrderPlanParams
from quant.engine.trades import round_trips, trade_stats
from quant.strategies.combo import Combo
from quant.strategies.events import EventCalendar
from quant.strategies.momentum_select import MomentumParams, rank_momentum
from quant.strategies.universe import Constituents

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "research" / "2026-10-30-combo.md"
CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
DATA_START = date(2013, 1, 2)  # 动量排序要 252 日历史；V2 要 120 日线 + 250 日重放
UNIVERSE_START = date(2014, 6, 1)
START = date(2015, 1, 2)
OOS_START = date(2023, 1, 3)
CASH = Decimal("100000")
MAX_MOVE = 0.6

BASE = "组合（持有过财报）"
EARNINGS_EXIT = "组合（财报前离场）"
CONFIGS: dict[str, dict[str, Any]] = {
    BASE: {},
    EARNINGS_EXIT: {"momentum": {"earnings_exit": True}},
    "相邻：比例 30/70": {"momentum_weight": 0.3, "swing_weight": 0.7},
    "相邻：比例 50/50": {"momentum_weight": 0.5, "swing_weight": 0.5},
    "相邻：阈值 25%": {"vol_threshold": 0.25},
    "相邻：阈值 35%": {"vol_threshold": 0.35},
    "相邻：动量 3 只": {"momentum": {"top_n": 3}},
    "相邻：动量 5 只": {"momentum": {"top_n": 5}},
    "单独：动量仓": {"momentum_weight": 1.0, "swing_weight": 0.0, "vol_scale": 1.0},
    "单独：V2 仓": {"momentum_weight": 0.0, "swing_weight": 1.0, "vol_scale": 1.0},
}
NEIGHBORS = [k for k in CONFIGS if k.startswith("相邻")]


# ---- 纯函数 ----


def missing_ratio_by_year(
    universe: Constituents, have: Collection[str], start: date, end: date
) -> pd.Series:
    """每年各成分快照中“没有数据的成员”占比的平均值（start 当时生效的快照计入 start 所在年）。"""
    days = sorted(universe.snapshots)
    rows: dict[int, list[float]] = {}
    for i, d in enumerate(days):
        nxt = days[i + 1] if i + 1 < len(days) else date.max
        if d > end or nxt <= start:
            continue
        members = universe.snapshots[d]
        ratio = sum(1 for s in members if s not in have) / len(members)
        rows.setdefault(max(d, start).year, []).append(ratio)
    return pd.Series({y: sum(v) / len(v) for y, v in sorted(rows.items())}, dtype=float)


def split_metrics(equity: pd.Series, oos_start: date) -> tuple[Metrics, Metrics, Metrics]:
    """全区间、调参区间（< oos_start）、样本外（从 oos_start 前一交易日收盘起算，保证连续）。"""
    ts = pd.Timestamp(oos_start)
    ins = equity[equity.index < ts]
    oos = equity[equity.index >= ins.index[-1]]
    return compute_metrics(equity), compute_metrics(ins), compute_metrics(oos)


# ---- 数据 ----


def fetch_constituents(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(CONSTITUENTS_URL, timeout=60) as resp:  # 固定的 https 地址
        path.write_bytes(resp.read())


def load_all_bars(
    symbols: Collection[str], end: date
) -> tuple[dict[str, pd.DataFrame], list[str], dict[str, str]]:
    """逐只拉取并校验；拿不到的记入 missing，校验未通过的记入 invalid（都排除）。"""
    settings = load_settings()
    store, provider, cal = (
        ParquetStore(settings.data_dir),
        YFinanceProvider(retries=2),
        get_calendar(),
    )
    bars: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    invalid: dict[str, str] = {}
    for i, s in enumerate(sorted(symbols)):
        try:
            bars |= ensure_bars(store, provider, cal, [s], DATA_START, end, max_move=MAX_MOVE)
        except DataUnavailableError:
            missing.append(s)
        except DataValidationError as e:
            invalid[s] = str(e).splitlines()[1] if "\n" in str(e) else str(e)
        if i % 50 == 0:
            print(f"  数据 {i}/{len(symbols)}：有 {len(bars)}，缺 {len(missing)}", flush=True)
    return bars, missing, invalid


def picked_symbols(
    bars: Mapping[str, pd.DataFrame], universe: Constituents, end: date, top: int = 5
) -> set[str]:
    """每个月末动量排名前 top 的并集（需要财报日的股票）。"""
    load = field_loader_from_bars(bars)
    syms = sorted(bars)
    close = load(syms, "close", DATA_START, end)
    volume = load(syms, "volume", DATA_START, end)
    p = MomentumParams()
    out: set[str] = set()
    for me in get_calendar().month_end_trading_days(START - timedelta(days=40), end):
        c = close.loc[: pd.Timestamp(me)].tail(p.history_needed + 5)
        v = volume.loc[: pd.Timestamp(me)].tail(p.history_needed + 5)
        out |= set(rank_momentum(c, v, universe.members(me), p)[:top])
    return out


def load_events(
    picked: Collection[str], user_agent: str, refresh: bool
) -> tuple[EventCalendar, int]:
    """V2 标的的 earnings.csv + 动量入选股票的财报日（缓存到 earnings_sp500.csv）。"""
    events_dir = load_settings().data_dir / "events"
    path = events_dir / "earnings_sp500.csv"
    if refresh or not path.exists():
        ciks = edgar.fetch_cik_map(user_agent)
        data: dict[str, list[date]] = {}
        for i, s in enumerate(sorted(picked)):
            if s not in ciks:
                continue
            try:
                data[s] = edgar.fetch_earnings(s, user_agent, ciks=(ciks[s],))
            except Exception as e:  # 单只失败不影响其他；报告里计入“无财报日”
                print(f"  财报 {s} 失败：{type(e).__name__}", flush=True)
            if i % 20 == 0:
                print(f"  财报 {i}/{len(picked)}", flush=True)
        save_earnings(path, data)
    earnings = {s: list(d) for s, d in load_earnings(path).items()}
    for s, d in load_earnings(events_dir / "earnings.csv").items():
        earnings[s] = sorted({*earnings.get(s, []), *d})
    no_data = sum(1 for s in picked if not earnings.get(s))
    merged = {s: tuple(d) for s, d in earnings.items()}
    return EventCalendar(earnings=merged, fomc=load_fomc_dates()), no_data


# ---- 回测（进程池） ----

_CTX: dict[str, Any] = {}


def _init(bars: Mapping[str, pd.DataFrame], events: EventCalendar, universe: Constituents) -> None:
    settings = load_settings()
    ex = settings.executor
    _CTX.update(
        bars=bars,
        events=events,
        universe=universe,
        costs=CostModel.from_settings(settings.costs),
        plan=OrderPlanParams(
            cash_buffer=Decimal(str(ex.cash_buffer)),
            min_trade_usd=Decimal(str(ex.min_trade_usd)),
            min_weight_diff=Decimal(str(ex.min_weight_diff)),
        ),
        cal=get_calendar(),
    )


def _run(args: tuple[str, dict[str, Any], date]) -> dict[str, Any]:
    name, params, end = args
    t0 = time.time()
    c = _CTX
    bt = Backtester(Combo(c["cal"]), c["bars"], c["cal"], c["costs"], CASH, c["plan"], params,
                    benchmark=None, events=c["events"], universe=c["universe"])  # fmt: skip
    res = bt.run(START, end)
    print(f"  {name} 完成，{time.time() - t0:.0f} 秒", flush=True)
    return {
        "name": name,
        "equity": res.equity,
        "turnover": res.metrics.turnover,
        "stats": trade_stats(round_trips(res.fills)),
    }


# ---- 报告 ----


def _pct(x: float) -> str:
    return "—" if x != x else f"{x:.1%}"


def _num(x: float) -> str:
    return "—" if x != x else f"{x:.2f}"


def _yearly(eq: pd.Series) -> pd.Series:
    y = eq.groupby(pd.DatetimeIndex(eq.index).year).agg(lambda s: s.iloc[-1])
    return y.pct_change().fillna(y.iloc[0] / eq.iloc[0] - 1)


def write_report(
    runs: dict[str, dict[str, Any]],
    bench: dict[str, pd.Series],
    end: date,
    universe: Constituents,
    missing_years: pd.Series,
    missing: list[str],
    invalid: dict[str, str],
    picked: int,
    no_earnings: int,
) -> bool:
    split = {k: split_metrics(r["equity"], OOS_START) for k, r in runs.items()}
    bsplit = {k: split_metrics(v, OOS_START) for k, v in bench.items()}
    q = bsplit["QQQ"]
    checks = combo_checks(*split[BASE][:1], q[0], split[BASE][1], q[1], split[BASE][2], q[2],
                          [split[k][0] for k in NEIGHBORS])  # fmt: skip
    passed = all(checks.values())

    def row(name: str, ms: tuple[Metrics, Metrics, Metrics], turnover: float | None) -> str:
        f, i, o = ms
        t = "—" if turnover is None else _num(turnover)
        return (f"| {name} | {_pct(f.total_return)} | {_pct(f.cagr)} | {_pct(f.max_drawdown)} | "
                f"{_num(f.sharpe)} | {_num(f.calmar)} | {_pct(f.volatility)} | {t} | "
                f"{_pct(i.cagr)} / {_num(i.sharpe)} / {_pct(i.max_drawdown)} | "
                f"{_pct(o.cagr)} / {_num(o.sharpe)} / {_pct(o.max_drawdown)} |")  # fmt: skip

    years = sorted(set(_yearly(runs[BASE]["equity"]).index))
    yearly_cols = [BASE, EARNINGS_EXIT, "单独：动量仓", "单独：V2 仓"]
    yearly = {k: _yearly(runs[k]["equity"]) for k in yearly_cols}
    yearly |= {k: _yearly(v) for k, v in bench.items()}
    snap_last = max(universe.snapshots)

    lines = [
        "# 组合策略 combo：正式回测与判定",
        "",
        f"生成时间：{datetime.now(UTC):%Y-%m-%d %H:%M} UTC。设计：`docs/superpowers/specs/"
        "2026-10-30-momentum-combo-design.md`；脚本：`scripts/research/combo.py`；issue #47。",
        "",
        f"区间 {START} ~ {end}；调参区间 {START} ~ 2022-12-30，样本外 {OOS_START} ~ {end}。"
        f"初始资金 {CASH}；成本为老虎固定式套餐 + 滑点；次日开盘成交。",
        "组合：40% 动量选股（标普 500 当时成分股，强势 + 放量前 4 只，月度）+ 60% 七姐妹 V2"
        "（120 日线向上、不用 PSQ）；QQQ 20 日年化波动率 > 30% 时整体半仓。",
        "",
        f"## 结论：{'通过' if passed else '未通过'}",
        "",
        f"判定对象：{BASE}。",
        "",
        "| 判定项 | 结果 |",
        "|---|---|",
        *[f"| {k} | {'✅' if v else '❌'} |" for k, v in checks.items()],
        "",
        "## 全部配置",
        "",
        "全区间指标 + 两个区间的“年化 / 夏普 / 最大回撤”。换手为年化单边换手（倍）。",
        "",
        "| 配置 | 累计 | 年化 | 最大回撤 | 夏普 | 卡玛 | 波动 | 换手 | 2015–2022 | 2023– |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *[row(k, split[k], runs[k]["turnover"]) for k in runs],
        *[row(f"基准：持有 {k}", bsplit[k], None) for k in bench],
        "",
        "## 动量仓财报处理对比（交给用户选择）",
        "",
        "| 年份 | " + " | ".join(yearly_cols + list(bench)) + " |",
        "|---|" + "---|" * (len(yearly_cols) + len(bench)),
        *[
            f"| {y} | "
            + " | ".join(
                _pct(float(yearly[k].get(y, float("nan")))) for k in yearly_cols + list(bench)
            )
            + " |"
            for y in years
        ],
        "",
        "## 交易统计（全区间，按 FIFO 配对的买卖）",
        "",
        "| 配置 | 笔数 | 胜率 | 盈亏比 | 平均持有天数 |",
        "|---|---|---|---|---|",
        *[
            f"| {k} | {int(r['stats']['count'])} | {_pct(r['stats']['win_rate'])} | "
            f"{_num(r['stats']['payoff'])} | {_num(r['stats']['avg_holding_days'])} |"
            for k, r in runs.items()
        ],
        "",
        "## 数据",
        "",
        f"- 成分名单：fja05680/sp500（最后一条变动 {snap_last}；之后视为不变）。",
        f"- 价格：yfinance 含分红复权；拿不到数据的 {len(missing)} 只、校验未通过的 "
        f"{len(invalid)} 只被排除。",
        f"- 动量曾入选（前 5）的股票 {picked} 只，其中没有财报日数据的 {no_earnings} 只"
        "（SEC 只能按当前代码查到 CIK；这些股票“财报前离场”不生效）。",
        "",
        "成分股中没有价格数据的比例（幸存者偏差的规模）：",
        "",
        "| 年份 | 缺失比例 |",
        "|---|---|",
        *[f"| {y} | {_pct(r)} |" for y, r in missing_years.items()],
        "",
        "校验未通过（单日涨跌超过 60%、缺交易日等）：",
        "",
        *([f"- {s}：{msg}" for s, msg in sorted(invalid.items())] or ["- 无"]),
        "",
        "## 已知局限",
        "",
        "- 幸存者偏差：已退市、被收购的股票大多拿不到数据（见上表），动量仓结果可能偏高。",
        "- 七姐妹 V2 仓的股票池是事后选的（#46 的结论），未来防守效果可能不如回测。",
        "- 样本外（2023 至今）在快速试验中已看过；比例、阈值是看过结果后从整数档中选的。"
        "真正的样本外检验是之后的模拟盘。",
        "- 实盘动量仓用新浪日线排序（不含分红复权），口径与回测略有差异，在第二份计划中处理。",
        "- 回测在次日开盘成交；实盘为 09:50 ET 按延迟行情限价单执行。",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-agent", required=True, help="SEC 要求的联系方式（只发给 SEC）")
    ap.add_argument("--constituents", type=Path, help="成分名单 CSV；缺省时下载")
    ap.add_argument("--end", type=date.fromisoformat, help="结束日（默认最近已收盘交易日）")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--refresh-earnings", action="store_true")
    args = ap.parse_args()
    settings = load_settings()
    cal = get_calendar()
    end = args.end or cal.last_completed_trading_day(datetime.now(UTC))

    path = args.constituents or settings.data_dir / "universe" / "sp500_history.csv"
    if not path.exists():
        print("下载成分名单…", flush=True)
        fetch_constituents(path)
    universe = load_constituents(path)

    combo = Combo(cal)
    fixed = set(combo.symbols({})) | {"SPY"}
    wanted = universe.all_symbols(UNIVERSE_START, end) | fixed
    print(f"准备数据：{len(wanted)} 只", flush=True)
    bars, missing, invalid = load_all_bars(wanted, end)
    lost = fixed - set(bars)
    if lost:
        raise SystemExit(f"固定标的缺数据：{sorted(lost)}")
    missing_years = missing_ratio_by_year(universe, set(bars), START, end)

    picked = picked_symbols(bars, universe, end)
    print(f"动量曾入选 {len(picked)} 只；准备财报日…", flush=True)
    events, no_earnings = load_events(picked, args.user_agent, args.refresh_earnings)

    t0 = time.time()
    jobs = [(k, v, end) for k, v in CONFIGS.items()]
    with ProcessPoolExecutor(args.workers, initializer=_init,
                             initargs=(bars, events, universe)) as pool:  # fmt: skip
        runs = {r["name"]: r for r in pool.map(_run, jobs)}
    print(f"回测 {len(runs)} 组，耗时 {time.time() - t0:.0f} 秒", flush=True)
    saved = settings.data_dir / "research" / "combo_runs.pkl"  # 报告出错时可复用，不入库
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(pickle.dumps(runs))

    def px(s: str) -> pd.Series:
        a = bars[s]["adj_close"]
        return a[(a.index >= pd.Timestamp(START)) & (a.index <= pd.Timestamp(end))]

    bench = {s: buy_and_hold(px(s), float(CASH)) for s in ("QQQ", "SPY")}
    passed = write_report(runs, bench, end, universe, missing_years, missing, invalid,
                          len(picked), no_earnings)  # fmt: skip
    print(f"结论：{'通过' if passed else '未通过'}；报告：{REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
