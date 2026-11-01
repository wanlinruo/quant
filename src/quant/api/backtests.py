"""提交回测：面板唯一的写操作（只插入一条 queued 任务，由 backtest-worker 执行）。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from quant.api.schemas import BacktestSubmit
from quant.core.settings import CostSettings
from quant.data.store import is_valid_symbol
from quant.db.backtests import create_run
from quant.engine.runner import CASH_KEY, COSTS_KEY
from quant.strategies import STRATEGIES, get_strategy
from quant.strategies.etf_momentum import MomentumParams

# 取值范围与改版前的面板表单一致
CASH_RANGE = (Decimal(1000), Decimal(10_000_000))
INT_RANGES = {"lookback": ("动量回看", 5, 504), "top_k": ("持有数量", 1, 10),
              "sma_window": ("均线窗口", 5, 400)}  # fmt: skip


FORM_STRATEGY = "etf_momentum"  # 表单里的标的池与动量参数只属于这个策略


def build_submission(
    req: BacktestSubmit,
    configured: CostSettings,
    last: date,
    *,
    data_end: date | None = None,
    strategy_params: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """把请求整理成 backtest_runs.params；返回 (params, 错误列表)。

    last 为最近一个已完成交易日；data_end 为服务器回测数据截止日（tiingo 模式）。
    etf_momentum 用表单参数；其它策略（如 combo）不用表单参数，取 strategy_params
    （服务器配置中该策略的参数，与模拟盘一致），没有时用策略默认值。
    """
    errors: list[str] = []
    if req.strategy not in STRATEGIES:
        errors.append(f"策略：不支持 {req.strategy!r}")
    if not CASH_RANGE[0] <= req.cash <= CASH_RANGE[1]:
        errors.append(f"初始资金：须在 {CASH_RANGE[0]} 到 {CASH_RANGE[1]} 美元之间")
    if req.end > last:
        errors.append(f"结束日期：不能晚于最近一个已完成交易日 {last}")
    if data_end is not None and req.end > data_end:
        errors.append(f"结束日期：不能晚于回测数据截止日 {data_end}")
    if req.start >= req.end:
        errors.append("开始日期必须早于结束日期")
    if req.strategy == FORM_STRATEGY:
        params, form_errors = _form_params(req)
        errors += form_errors
    else:
        params = dict(strategy_params or {})
        if req.strategy in STRATEGIES:
            try:
                get_strategy(req.strategy).warmup_days(params)  # 校验参数
            except (ValueError, TypeError) as e:
                errors.append(f"策略参数：{e}")
    errors += _apply_costs(params, req, configured)
    params[CASH_KEY] = str(req.cash)
    return params, errors


def _form_params(req: BacktestSubmit) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    universe = [x.strip().upper() for x in req.universe]
    safe_asset = req.safe_asset.strip().upper()
    if bad := [x for x in universe if not is_valid_symbol(x)]:
        errors.append(f"标的池：代码不合法 {bad}")
    if not is_valid_symbol(safe_asset):
        errors.append(f"避险资产：代码不合法 {safe_asset!r}")
    for field, (label, lo, hi) in INT_RANGES.items():
        if not lo <= getattr(req, field) <= hi:
            errors.append(f"{label}：须在 {lo} 到 {hi} 之间")
    params: dict[str, Any] = {
        "universe": universe,
        "safe_asset": safe_asset,
        "lookback": req.lookback,
        "top_k": req.top_k,
        "sma_window": req.sma_window,
    }
    try:
        MomentumParams.from_mapping(params)
    except (ValueError, TypeError) as e:
        errors.append(f"策略参数：{e}")
    return params, errors


def _apply_costs(
    params: dict[str, Any], req: BacktestSubmit, configured: CostSettings
) -> list[str]:
    errors: list[str] = []
    # 面板只覆盖佣金、最低佣金和滑点；平台费与封顶沿用配置（与命令行回测一致）
    overrides = {
        "commission_per_share": req.costs.commission_per_share,
        "min_commission": req.costs.min_commission,
        "slippage": req.costs.slippage,
    }
    try:
        costs = CostSettings(**{**configured.model_dump(), **overrides})
    except ValidationError as e:
        errors.append(f"成本参数：{e.errors()[0]['msg']}")
        costs = configured
    if costs.commission_per_share is None or costs.min_commission is None:
        errors.append("请填写佣金（老虎实际费率待确认，见 issue #5）")
    params[COSTS_KEY] = {k: str(v) for k, v in costs.model_dump().items() if v is not None}
    return errors


def submit(
    session: Session,
    req: BacktestSubmit,
    configured: CostSettings,
    last: date,
    *,
    data_end: date | None = None,
    strategy_params: Mapping[str, Any] | None = None,
) -> int:
    """校验并插入一条 queued 任务；事务由调用方用 session.begin() 提交。"""
    params, errors = build_submission(
        req, configured, last, data_end=data_end, strategy_params=strategy_params
    )
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return create_run(session, req.strategy, params, req.start, req.end, "queued")
