"""回测执行流程：准备数据 → 回测 → 结果入库。命令行与 backtest-worker 共用。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from quant.broker.sim import CostModel
from quant.core.calendar import TradingCalendar, get_calendar
from quant.core.settings import CostSettings, Settings
from quant.data.backtest_data import (
    FULL_START,
    MIN_BACKTEST_START,
    BackfillState,
    backtest_store,
    earnings_path,
    state_path,
)
from quant.data.events import live_earnings_path, load_earnings, load_fomc_dates
from quant.data.pipeline import DataValidationError, ensure_bars
from quant.data.providers.base import DataProvider, DataUnavailableError
from quant.data.providers.yfinance import YFinanceProvider
from quant.data.store import ParquetStore
from quant.data.universe import constituents_path, load_constituents
from quant.db.backtests import complete_run
from quant.engine.backtester import Backtester, BacktestResult
from quant.engine.coverage import missing_ratio_by_year
from quant.engine.orders import OrderPlanParams
from quant.strategies import get_strategy
from quant.strategies.events import EventCalendar

BENCHMARK = "SPY"
COSTS_KEY = "_costs"  # 存入 backtest_runs.params 的成本假设
CASH_KEY = "_cash"


@dataclass(frozen=True)
class BacktestSpec:
    strategy: str
    start: date
    end: date
    costs: CostModel
    cash: Decimal = Decimal("100000")
    params: Mapping[str, Any] = field(default_factory=dict)

    def stored_params(self) -> dict[str, Any]:
        """写入 backtest_runs.params：策略参数 + 成本与资金（以 _ 开头的保留键）。"""
        return {
            **self.params,
            COSTS_KEY: {
                k: str(v) for k, v in self.costs.to_settings().model_dump().items() if v is not None
            },
            CASH_KEY: str(self.cash),
        }

    @classmethod
    def from_stored(
        cls, strategy: str, start: date, end: date, stored: Mapping[str, Any], settings: Settings
    ) -> BacktestSpec:
        """从 backtest_runs 的一行还原；未记录成本时使用配置（佣金未配置则报错，见 #5）。"""
        params = {k: v for k, v in stored.items() if not k.startswith("_")}
        raw_costs = stored.get(COSTS_KEY)
        if raw_costs:
            costs = CostModel.from_settings(CostSettings(**raw_costs))
        else:
            costs = CostModel.from_settings(settings.costs)
        cash = Decimal(str(stored.get(CASH_KEY, "100000")))
        return cls(strategy, start, end, costs, cash, params)


def costs_with_overrides(
    settings: Settings,
    commission_per_share: Decimal | None = None,
    min_commission: Decimal | None = None,
    slippage: Decimal | None = None,
) -> CostModel:
    """配置中的成本，命令行或面板给出的佣金、滑点覆盖对应项；平台费与封顶始终用配置。"""
    c = settings.costs
    overrides = {
        "commission_per_share": commission_per_share,
        "min_commission": min_commission,
        "slippage": slippage,
    }
    return CostModel.from_settings(
        c.model_copy(update={k: v for k, v in overrides.items() if v is not None})
    )


class BacktestDataError(ValueError):
    """回测所需的本地数据不全（服务器 tiingo 模式下不联网补拉）。"""


def _local_bars(
    store: ParquetStore, symbols: Iterable[str], start: date, end: date, cal: TradingCalendar
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """只读本地：返回有数据且覆盖到 end 的标的，以及缺失或不覆盖的标的。

    上市晚于 start 的标的照常使用（补数时已取全部历史并校验过）。
    """
    last_needed = end if cal.is_trading_day(end) else cal.previous_trading_day(end)
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for s in sorted(set(symbols)):
        df = store.read_bars(s, start, end)
        if df.empty or pd.Timestamp(df.index[-1]).date() < last_needed:
            missing.append(s)
        else:
            out[s] = df
    return out, missing


def _backtest_events(data_dir: Path, tiingo: bool) -> tuple[EventCalendar, str]:
    """回测用的财报日：earnings.csv ∪ earnings_sp500.csv（SEC EDGAR）∪ earnings_live.csv + FOMC。"""
    earnings: dict[str, set[date]] = {}
    sp500 = earnings_path(data_dir)
    for p in (data_dir / "events" / "earnings.csv", sp500, live_earnings_path(data_dir)):
        if p.exists():
            for sym, days in load_earnings(p).items():
                earnings.setdefault(sym, set()).update(days)
    cal = EventCalendar(
        earnings={k: tuple(sorted(v)) for k, v in earnings.items()}, fomc=load_fomc_dates()
    )
    if not sp500.exists():
        flag = "none"
    elif tiingo:  # 服务器：同步分批进行，整轮完成后才算完整
        flag = "complete" if BackfillState.load(state_path(data_dir)).earnings_month else "partial"
    else:
        flag = "complete"
    return cal, flag


def run_spec(
    spec: BacktestSpec,
    settings: Settings,
    provider: DataProvider | None = None,
    refresh_data: bool = False,
) -> BacktestResult:
    """准备数据并回测。

    data.backtest_source = yfinance（Mac 默认）：主数据目录，缺数据时用 YFinance 拉取。
    data.backtest_source = tiingo（服务器）：只读 backtest_bars 目录，不联网；缺固定标的直接报错。
    需要候选池的策略（如 combo）另外加载成分股快照、候选池日线与历史财报日，并记录覆盖率。
    """
    if spec.start >= spec.end:
        raise ValueError(f"start {spec.start} 必须早于 end {spec.end}")
    cal = get_calendar()
    strategy = get_strategy(spec.strategy)
    params = dict(spec.params)
    symbols = sorted({*strategy.symbols(params), BENCHMARK})
    # 交易日 ≈ 自然日 × 252/365，再多留余量
    data_start = spec.start - timedelta(days=int(strategy.warmup_days(params) * 1.5) + 10)
    tiingo = settings.data.backtest_source == "tiingo"
    data_dir = settings.data_dir
    if tiingo:
        store = backtest_store(data_dir)
        bars, missing = _local_bars(store, symbols, data_start, spec.end, cal)
        if missing:
            raise BacktestDataError(
                f"回测数据尚未补齐：缺 {', '.join(missing)}（服务器在空闲时按 Tiingo 额度补齐，"
                "可用 quant backtest-data status 查看进度）"
            )
        _check_start(bars, data_start, cal)
    else:
        store = ParquetStore(data_dir)
        bars = ensure_bars(
            store, provider or YFinanceProvider(), cal, symbols, data_start, spec.end,
            refresh_data, strategy.max_daily_move,
        )  # fmt: skip
    data_end = min(pd.Timestamp(df.index[-1]).date() for df in bars.values())
    info: dict[str, Any] = {"source": "tiingo" if tiingo else "yfinance",
                            "data_end": data_end.isoformat()}  # fmt: skip
    events: EventCalendar | None = None
    universe = None
    if strategy.uses_universe:
        path = constituents_path(data_dir)
        if not path.exists():
            raise BacktestDataError(f"缺成分股名单：{path}")
        universe = load_constituents(path)
        members = universe.all_symbols(data_start, spec.end) - set(symbols)
        if tiingo:
            cand, _ = _local_bars(store, members, data_start, spec.end, cal)
            # 已退市成分的数据停在退市日：只要在 data_start 之后有数据就算有
            cand |= {
                s: df for s in members - set(cand)
                if not (df := store.read_bars(s, data_start, spec.end)).empty
            }  # fmt: skip
        else:
            cand = {}
            for s in sorted(members):
                try:
                    cand |= ensure_bars(store, provider or YFinanceProvider(), cal, [s],
                                        data_start, spec.end, refresh_data,
                                        strategy.max_daily_move)  # fmt: skip
                except (DataUnavailableError, DataValidationError):
                    continue
        bars = {**cand, **bars}
        ranges = {s: (pd.Timestamp(df.index[0]).date(), pd.Timestamp(df.index[-1]).date())
                  for s, df in bars.items()}  # fmt: skip
        by_year = missing_ratio_by_year(universe, set(bars), spec.start, spec.end, ranges)
        events, earnings_flag = _backtest_events(data_dir, tiingo)
        info |= {
            "coverage": float(1 - by_year.mean()) if len(by_year) else 1.0,
            "missing_by_year": {str(y): float(v) for y, v in by_year.items()},
            "earnings": earnings_flag,
        }
    ex = settings.executor
    plan = OrderPlanParams(
        cash_buffer=Decimal(str(ex.cash_buffer)),
        min_trade_usd=Decimal(str(ex.min_trade_usd)),
        min_weight_diff=Decimal(str(ex.min_weight_diff)),
    )
    bt = Backtester(strategy, bars, cal, spec.costs, spec.cash, plan, params, BENCHMARK,
                    events=events, universe=universe)  # fmt: skip
    result = bt.run(spec.start, spec.end)
    stale = _stale_holdings(result.weights, bars, spec.end, cal)
    if stale:
        info["stale_holdings"] = stale
    return replace(result, data_info=info)


def _check_start(bars: Mapping[str, pd.DataFrame], data_start: date, cal: TradingCalendar) -> None:
    """本地数据从 FULL_START 起：早于此时就存在的标的若首日晚于预热起点，说明开始日期过早。"""
    need = data_start if cal.is_trading_day(data_start) else cal.next_trading_day(data_start)
    for s, df in bars.items():
        first = pd.Timestamp(df.index[0]).date()
        if first > need + timedelta(days=7) and first <= FULL_START + timedelta(days=15):
            raise BacktestDataError(
                f"开始日期过早：服务器回测数据从 {FULL_START} 起（{s} 最早 {first}），"
                f"策略预热需要 {data_start} 起的数据；请把开始日期设为 {MIN_BACKTEST_START} 之后"
            )


def _stale_holdings(
    weights: pd.DataFrame, bars: Mapping[str, pd.DataFrame], end: date, cal: TradingCalendar
) -> dict[str, str]:
    """区间结束时仍持有、但数据在结束前已中断的标的（多为退市）：按最后价格冻结，结果偏乐观。"""
    if weights.empty:
        return {}
    last_day = end if cal.is_trading_day(end) else cal.previous_trading_day(end)
    final = weights.iloc[-1]
    out: dict[str, str] = {}
    for s, w in final.items():
        df = bars.get(str(s))
        if w > 1e-9 and df is not None and len(df):
            last = pd.Timestamp(df.index[-1]).date()
            if last < last_day:
                out[str(s)] = last.isoformat()
    return out


def save_result(session: Session, run_id: int, result: BacktestResult) -> None:
    metrics = {
        **result.metrics.to_dict(),
        "benchmark": result.benchmark.to_dict() if result.benchmark else None,
    }
    if result.data_info:
        metrics["data"] = result.data_info
    complete_run(session, run_id, metrics, result.equity, result.drawdown)
