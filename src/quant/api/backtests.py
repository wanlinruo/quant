"""提交回测：面板唯一的写操作（只插入一条 queued 任务，由 backtest-worker 执行）。"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from quant.api.schemas import BacktestSubmit, ParamField
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
    min_start: date | None = None,
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
    if min_start is not None and req.start < min_start:
        errors.append(f"开始日期：不能早于 {min_start}（服务器回测数据从 2013 年起，含策略预热）")
    if req.strategy == FORM_STRATEGY:
        params, form_errors = _form_params(req)
        errors += form_errors
    else:
        params = _merge(strategy_params or {}, req.params or {})
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
    min_start: date | None = None,
) -> int:
    """校验并插入一条 queued 任务；事务由调用方用 session.begin() 提交。"""
    params, errors = build_submission(
        req, configured, last, data_end=data_end, strategy_params=strategy_params,
        min_start=min_start,
    )  # fmt: skip
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return create_run(session, req.strategy, params, req.start, req.end, "queued")


def _merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """服务器配置的参数被用户的覆盖合并：嵌套组（dict）按键合并，其余直接替换。"""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _field_type(v: Any) -> str | None:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list | tuple):
        return "list"
    return None


def _fields(cls: type, values: Mapping[str, Any], group: str | None) -> list[ParamField]:
    out = []
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            default: Any = f.default
        elif f.default_factory is not dataclasses.MISSING:
            default = f.default_factory()
        else:
            continue
        v = values.get(f.name, default)
        t = _field_type(v)
        if t is None:  # 嵌套组等由调用方另行展开
            continue
        out.append(ParamField(key=f.name, group=group, type=t,
                              default=list(v) if isinstance(v, tuple) else v))  # fmt: skip
    return out


def param_schema(strategy_name: str, configured: Mapping[str, Any]) -> dict[str, list[ParamField]]:
    """各策略的参数表单；configured 为服务器配置的策略参数（只用于名为 strategy_name 的策略）。"""
    from quant.strategies.combo import V2_SWING, ComboParams
    from quant.strategies.etf_momentum import MomentumParams as EtfParams
    from quant.strategies.momentum_select import MomentumParams as SelectParams
    from quant.strategies.swing_pullback import SwingParams

    def cfg(name: str) -> Mapping[str, Any]:
        return configured if strategy_name == name else {}

    c = cfg("combo")
    return {
        "combo": [
            *_fields(ComboParams, c, None),
            *_fields(SelectParams, c.get("momentum") or {}, "momentum"),
            *_fields(SwingParams, {**V2_SWING, **(c.get("swing") or {})}, "swing"),
        ],
        "etf_momentum": _fields(EtfParams, cfg("etf_momentum"), None),
        "momentum_select": _fields(SelectParams, cfg("momentum_select"), None),
        "swing_pullback": _fields(SwingParams, cfg("swing_pullback"), None),
    }
