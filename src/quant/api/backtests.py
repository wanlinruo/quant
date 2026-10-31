"""提交回测：面板唯一的写操作（只插入一条 queued 任务，由 backtest-worker 执行）。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from quant.api.schemas import BacktestSubmit
from quant.core.settings import CostSettings
from quant.db.backtests import create_run
from quant.engine.runner import CASH_KEY, COSTS_KEY
from quant.strategies.etf_momentum import MomentumParams


def build_submission(
    req: BacktestSubmit, configured: CostSettings
) -> tuple[dict[str, Any], list[str]]:
    """把请求整理成 backtest_runs.params；返回 (params, 错误列表)。"""
    errors: list[str] = []
    params: dict[str, Any] = {
        "universe": list(req.universe),
        "safe_asset": req.safe_asset,
        "lookback": req.lookback,
        "top_k": req.top_k,
        "sma_window": req.sma_window,
    }
    try:
        MomentumParams.from_mapping(params)
    except (ValueError, TypeError) as e:
        errors.append(f"策略参数：{e}")
    if req.start >= req.end:
        errors.append("开始日期必须早于结束日期")
    try:
        costs = CostSettings(
            commission_per_share=req.costs.commission_per_share,
            min_commission=req.costs.min_commission,
            slippage=req.costs.slippage,
        )
    except ValidationError as e:
        errors.append(f"成本参数：{e.errors()[0]['msg']}")
        costs = configured
    if costs.commission_per_share is None or costs.min_commission is None:
        errors.append("请填写佣金（老虎实际费率待确认，见 issue #5）")
    params[COSTS_KEY] = {
        "commission_per_share": str(costs.commission_per_share),
        "min_commission": str(costs.min_commission),
        "slippage": str(costs.slippage),
    }
    params[CASH_KEY] = str(req.cash)
    return params, errors


def submit(session: Session, req: BacktestSubmit, configured: CostSettings) -> int:
    """校验并插入一条 queued 任务；事务由调用方用 session.begin() 提交。"""
    params, errors = build_submission(req, configured)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return create_run(session, req.strategy, params, req.start, req.end, "queued")
