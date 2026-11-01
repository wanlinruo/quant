"""回测提交的策略参数严格校验（#67 C 批）：按各策略参数数据类的字段注解检查类型与范围。

只用于面板提交的参数覆盖：未知键、类型不符、非有限数、超出范围、非法代码一律拒绝，
不改变策略参数类本身（模拟盘配置与命令行的行为不受影响）。
"""

from __future__ import annotations

import dataclasses
import json
import math
import typing
from collections.abc import Mapping
from typing import Any

from quant.data.store import is_valid_symbol

MAX_PARAMS_BYTES = 16_000
MAX_LIST = 50
MAX_STR = 64


def _classes(strategy: str) -> tuple[type | None, dict[str, type]]:
    from quant.strategies.combo import ComboParams
    from quant.strategies.etf_momentum import MomentumParams as EtfParams
    from quant.strategies.momentum_select import MomentumParams as SelectParams
    from quant.strategies.swing_pullback import SwingParams

    return {
        "combo": (ComboParams, {"momentum": SelectParams, "swing": SwingParams}),
        "momentum_select": (SelectParams, {}),
        "swing_pullback": (SwingParams, {}),
        "etf_momentum": (EtfParams, {}),
    }.get(strategy, (None, {}))


def _default(f: dataclasses.Field[Any]) -> Any:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return None


def _check(name: str, annot: Any, default: Any, v: Any) -> tuple[Any, str | None]:
    """返回（规范化后的值, 错误）。"""
    origin = typing.get_origin(annot)
    if origin is typing.Literal:
        allowed = typing.get_args(annot)
        return (v, None) if v in allowed else (v, f"{name} 须为 {list(allowed)} 之一")
    if annot is bool:
        return (v, None) if isinstance(v, bool) else (v, f"{name} 须为 true / false")
    if annot is int:
        if not isinstance(v, int) or isinstance(v, bool):
            return v, f"{name} 须为整数"
        low = 1 if isinstance(default, int) and default >= 1 else 0
        return (v, None) if v >= low else (v, f"{name} 须 ≥ {low}")
    if annot is float:
        if not isinstance(v, int | float) or isinstance(v, bool) or not math.isfinite(v):
            return v, f"{name} 须为有限数字"
        if v < 0:
            return v, f"{name} 不能为负"
        if isinstance(default, float) and default < 1 and v > 1:
            return v, f"{name} 须在 0 到 1 之间"
        return float(v), None
    if annot is str:
        if not isinstance(v, str) or not v or len(v) > MAX_STR:
            return v, f"{name} 须为非空文字"
        if isinstance(default, str) and is_valid_symbol(default):  # 代码类字段
            sym = v.strip().upper()
            return (sym, None) if is_valid_symbol(sym) else (v, f"{name} 不是合法代码：{v}")
        return v, None
    if origin in (tuple, list):
        if not isinstance(v, list) or not v or len(v) > MAX_LIST:
            return v, f"{name} 须为 1 到 {MAX_LIST} 个代码的列表"
        out = []
        for x in v:
            sym = x.strip().upper() if isinstance(x, str) else ""
            if not is_valid_symbol(sym):
                return v, f"{name} 含非法代码：{x!r}"
            out.append(sym)
        return out, None
    return v, f"{name} 不支持在面板修改"


def _check_class(cls: type, params: Mapping[str, Any], prefix: str, groups: Mapping[str, type],
                 errors: list[str]) -> dict[str, Any]:  # fmt: skip
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    out: dict[str, Any] = {}
    for k, v in params.items():
        name = f"{prefix}{k}"
        if k in groups:
            if not isinstance(v, Mapping):
                errors.append(f"{name} 须为参数组")
                continue
            out[k] = _check_class(groups[k], v, f"{name}.", {}, errors)
            continue
        if k not in fields:
            errors.append(f"未知参数：{name}")
            continue
        val, err = _check(name, hints.get(k), _default(fields[k]), v)
        if err:
            errors.append(err)
        else:
            out[k] = val
    return out


def check_params(strategy: str, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """校验面板提交的参数覆盖；返回（规范化后的参数, 错误列表）。"""
    try:
        size = len(json.dumps(params, allow_nan=False))
    except (TypeError, ValueError):
        return {}, ["参数含无法保存的值（如 NaN）"]
    if size > MAX_PARAMS_BYTES:
        return {}, [f"参数过大（{size} 字节，上限 {MAX_PARAMS_BYTES}）"]
    cls, groups = _classes(strategy)
    if cls is None:
        return {}, [f"策略 {strategy} 不支持参数覆盖"]
    errors: list[str] = []
    out = _check_class(cls, params, "", groups, errors)
    return out, errors
