"""回测执行流程：准备数据 → 回测 → 结果入库。命令行与 backtest-worker 共用。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.core.settings import CostSettings, Settings
from quant.data.pipeline import ensure_bars
from quant.data.providers.base import DataProvider
from quant.data.providers.yfinance import YFinanceProvider
from quant.data.store import ParquetStore
from quant.db.backtests import complete_run
from quant.engine.backtester import Backtester, BacktestResult
from quant.engine.orders import OrderPlanParams
from quant.strategies import get_strategy

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
                "commission_per_share": str(self.costs.commission_per_share),
                "min_commission": str(self.costs.min_commission),
                "slippage": str(self.costs.slippage),
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
    c = settings.costs
    return CostModel.from_settings(
        CostSettings(
            commission_per_share=commission_per_share
            if commission_per_share is not None
            else c.commission_per_share,
            min_commission=min_commission if min_commission is not None else c.min_commission,
            slippage=slippage if slippage is not None else c.slippage,
        )
    )


def run_spec(
    spec: BacktestSpec,
    settings: Settings,
    provider: DataProvider | None = None,
    refresh_data: bool = False,
) -> BacktestResult:
    if spec.start >= spec.end:
        raise ValueError(f"start {spec.start} 必须早于 end {spec.end}")
    cal = get_calendar()
    strategy = get_strategy(spec.strategy)
    params = dict(spec.params)
    symbols = sorted({*strategy.symbols(params), BENCHMARK})
    # 交易日 ≈ 自然日 × 252/365，再多留余量
    data_start = spec.start - timedelta(days=int(strategy.warmup_days(params) * 1.5) + 10)
    bars = ensure_bars(
        ParquetStore(settings.data_dir),
        provider or YFinanceProvider(),
        cal,
        symbols,
        data_start,
        spec.end,
        refresh_data,
    )
    ex = settings.executor
    plan = OrderPlanParams(
        cash_buffer=Decimal(str(ex.cash_buffer)),
        min_trade_usd=Decimal(str(ex.min_trade_usd)),
        min_weight_diff=Decimal(str(ex.min_weight_diff)),
    )
    bt = Backtester(strategy, bars, cal, spec.costs, spec.cash, plan, params, BENCHMARK)
    return bt.run(spec.start, spec.end)


def save_result(session: Session, run_id: int, result: BacktestResult) -> None:
    metrics = {
        **result.metrics.to_dict(),
        "benchmark": result.benchmark.to_dict() if result.benchmark else None,
    }
    complete_run(session, run_id, metrics, result.equity, result.drawdown)
