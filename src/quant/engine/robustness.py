"""稳健性检验的子回测计划（#68，设计 7.3）：纯函数，不访问数据库。

父回测是 3×3 参数网格的中心格（不重复跑）。子任务：网格其余格、单独参数 ±1 档、
成本 ×0 / ×2 / ×3、起点推后 3 / 6 / 9 / 12 个月。
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import numpy as np
import pandas as pd

from quant.strategies.combo import V2_SWING, ComboParams
from quant.strategies.etf_momentum import MomentumParams as EtfParams
from quant.strategies.momentum_select import MomentumParams as SelectParams
from quant.strategies.swing_pullback import SwingParams

Kind = Literal["grid", "extra", "cost", "start"]
COST_MULTS = (0, 2, 3)
START_SHIFTS = (3, 6, 9, 12)  # 月
MIN_REMAINING_DAYS = 2 * 365
ROBUST_KEY = "_robust"  # 父任务 params 中的标记


@dataclass(frozen=True)
class Variant:
    kind: Kind
    label: str
    params: dict[str, Any]
    start: date
    cost_mult: float | None = None
    x: int | None = None  # 网格列 0..2
    y: int | None = None  # 网格行 0..2
    neighbor: bool = False  # 相邻参数逐个检查


@dataclass(frozen=True)
class Axis:
    path: tuple[str, ...]
    label: str
    step: float
    valid: Callable[[float, Mapping[str, Any]], bool]
    fmt: str = "{:g}"


def _defaults(cls: type) -> dict[str, Any]:
    return {
        f.name: f.default for f in dataclasses.fields(cls) if f.default is not dataclasses.MISSING
    }


# 每个策略：网格 X、Y 两轴与单独参数；嵌套参数的默认值
AXES: dict[str, tuple[Axis, Axis, list[Axis]]] = {
    "combo": (
        Axis(("momentum_weight",), "动量仓比例", 0.1, lambda v, p: 0 < v < _total(p), "{:.0%}"),
        Axis(("vol_threshold",), "择时波动阈值", 0.05, lambda v, p: v > 0, "{:.0%}"),
        [Axis(("momentum", "top_n"), "动量只数", 1, lambda v, p: v >= 1)],
    ),
    "etf_momentum": (
        Axis(("lookback",), "动量回看天数", 42, lambda v, p: v >= 21),
        Axis(
            ("top_k",),
            "持有只数",
            1,
            lambda v, p: 1 <= v <= len(p.get("universe") or EtfParams().universe),
        ),
        [Axis(("sma_window",), "均线窗口", 50, lambda v, p: v >= 20)],
    ),
    "momentum_select": (
        Axis(("lookback",), "动量回看天数", 42, lambda v, p: v >= 21),
        Axis(("top_n",), "持有只数", 1, lambda v, p: v >= 1),
        [Axis(("trend_ma",), "趋势均线", 40, lambda v, p: v >= 20)],
    ),
    "swing_pullback": (
        Axis(("stop_pct",), "止损幅度", 0.02, lambda v, p: v > 0, "{:.0%}"),
        Axis(("max_hold",), "最长持有天数", 10, lambda v, p: v >= 5),
        [Axis(("support_ma",), "支撑均线", 5, lambda v, p: v >= 5)],
    ),
}
DEFAULTS: dict[str, dict[str, Any]] = {
    "combo": {
        **_defaults(ComboParams),
        "momentum": _defaults(SelectParams),
        "swing": dict(V2_SWING),
    },
    "etf_momentum": _defaults(EtfParams),
    "momentum_select": _defaults(SelectParams),
    "swing_pullback": _defaults(SwingParams),
}


def supported(strategy: str) -> bool:
    return strategy in AXES


def _total(p: Mapping[str, Any]) -> float:
    return float(p.get("momentum_weight", 0.4)) + float(p.get("swing_weight", 0.6))


def _get(params: Mapping[str, Any], defaults: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = params
    for k in path:
        cur = cur.get(k) if isinstance(cur, Mapping) else None
    if cur is not None:
        return cur
    d: Any = defaults
    for k in path:
        d = d[k]
    return d


def _set(params: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    out = dict(params)
    if len(path) == 1:
        out[path[0]] = value
    else:
        out[path[0]] = _set(dict(out.get(path[0]) or {}), path[1:], value)
    return out


def _value(base: Any, step: float, k: int) -> Any:
    v = base + step * k
    return int(v) if isinstance(base, int) and not isinstance(base, bool) else round(float(v), 6)


def _apply(strategy: str, params: dict[str, Any], axis: Axis, value: Any) -> dict[str, Any]:
    out = _set(params, axis.path, value)
    if strategy == "combo" and axis.path == ("momentum_weight",):
        full = {**DEFAULTS["combo"], **params}
        out["swing_weight"] = round(_total(full) - value, 6)
    return out


def _add_months(d: date, m: int) -> date:
    y, mo = divmod(d.month - 1 + m, 12)
    return date(d.year + y, mo + 1, min(d.day, 28))


def plan(strategy: str, params: Mapping[str, Any], start: date, end: date) -> list[Variant]:
    if strategy not in AXES:
        raise ValueError(f"{strategy} 没有稳健性检验计划")
    ax, ay, extras = AXES[strategy]
    base = {k: v for k, v in params.items() if not k.startswith("_")}
    full = {**DEFAULTS[strategy], **base}
    bx, by = _get(base, DEFAULTS[strategy], ax.path), _get(base, DEFAULTS[strategy], ay.path)
    out: list[Variant] = []
    for i in range(3):
        for j in range(3):
            if (i, j) == (1, 1):
                continue
            vx, vy = _value(bx, ax.step, i - 1), _value(by, ay.step, j - 1)
            if not (ax.valid(vx, full) and ay.valid(vy, full)):
                continue
            p = _apply(strategy, _apply(strategy, base, ax, vx), ay, vy)
            label = f"{ax.label} {ax.fmt.format(vx)} · {ay.label} {ay.fmt.format(vy)}"
            out.append(Variant("grid", label, p, start, x=i, y=j, neighbor=(i == 1) != (j == 1)))
    for e in extras:
        b = _get(base, DEFAULTS[strategy], e.path)
        for k in (-1, 1):
            v = _value(b, e.step, k)
            if e.valid(v, full):
                p = _apply(strategy, base, e, v)
                out.append(
                    Variant("extra", f"{e.label} {e.fmt.format(v)}", p, start, neighbor=True)
                )
    for m in COST_MULTS:
        out.append(Variant("cost", f"成本 ×{m}", dict(base), start, cost_mult=m))
    for m in START_SHIFTS:
        s = _add_months(start, m)
        if (end - s).days >= MIN_REMAINING_DAYS:
            out.append(Variant("start", f"起点 {s}", dict(base), s))
    return out


def skipped_neighbor_labels(strategy: str, params: Mapping[str, Any]) -> list[str]:
    """因参数边界未生成的相邻设置；用于解释判定为何只包含部分相邻项。"""
    if strategy not in AXES:
        return []
    ax, ay, extras = AXES[strategy]
    base = {k: v for k, v in params.items() if not k.startswith("_")}
    full = {**DEFAULTS[strategy], **base}
    bx, by = _get(base, DEFAULTS[strategy], ax.path), _get(base, DEFAULTS[strategy], ay.path)
    skipped: list[str] = []
    for i, j in ((0, 1), (2, 1), (1, 0), (1, 2)):
        vx, vy = _value(bx, ax.step, i - 1), _value(by, ay.step, j - 1)
        if not (ax.valid(vx, full) and ay.valid(vy, full)):
            skipped.append(f"{ax.label} {ax.fmt.format(vx)} · {ay.label} {ay.fmt.format(vy)}")
    for extra in extras:
        current = _get(base, DEFAULTS[strategy], extra.path)
        for direction in (-1, 1):
            value = _value(current, extra.step, direction)
            if not extra.valid(value, full):
                skipped.append(f"{extra.label} {extra.fmt.format(value)}")
    return skipped


def scale_costs(raw: Mapping[str, str], mult: float) -> dict[str, str]:
    """按倍数放大费率、最低收费与滑点；max_fee_pct 是上限，不变。"""
    out = {}
    for k, v in raw.items():
        if k == "max_fee_pct":
            out[k] = v
        else:
            d = Decimal(v)
            out[k] = str((d * Decimal(str(mult))).quantize(d))
    return out


def grid_axes(
    strategy: str, params: Mapping[str, Any]
) -> tuple[tuple[str, list[str]], tuple[str, list[str]]]:
    """网格两轴的名称与 3 个取值（显示用，中间为父回测的值）。"""
    ax, ay, _ = AXES[strategy]
    base = {k: v for k, v in params.items() if not k.startswith("_")}
    out = []
    for a in (ax, ay):
        b = _get(base, DEFAULTS[strategy], a.path)
        out.append((a.label, [a.fmt.format(_value(b, a.step, k)) for k in (-1, 0, 1)]))
    return out[0], out[1]


# ---- walk-forward：用网格各格的净值逐年滚动选参 ----

TRAIN_YEARS = 3


@dataclass(frozen=True)
class WfSegment:
    year: int
    chosen: str
    test_return: float
    base_return: float


@dataclass(frozen=True)
class WalkForward:
    segments: list[WfSegment]
    oos: pd.Series  # 拼接的样本外净值（起点 1）
    base: pd.Series  # 父回测同期净值（起点 1）


def _sharpe(r: pd.Series) -> float:
    sd = float(r.std())
    return float(r.mean()) / sd * math.sqrt(252) if sd > 0 else float("-inf")


def _ret(r: pd.Series) -> float:
    return float(np.prod(1 + r.to_numpy(dtype=float)) - 1)


def _year(r: pd.Series, year: int) -> pd.Series:
    return r.loc[pd.DatetimeIndex(r.index).year == year]


def walk_forward(
    series: Mapping[str, pd.Series], base: str, train_years: int = TRAIN_YEARS
) -> WalkForward | None:
    """每个测试年选训练期（前 train_years 个完整日历年）夏普最高的一格，取其该年收益。

    series 为各格净值（日期索引，含父回测 base）；历史不足 train_years + 1 年时返回 None。
    """
    rets = {k: s.sort_index().pct_change().dropna() for k, s in series.items() if len(s) > 1}
    if base not in rets:
        return None
    first = max(r.index[0] for r in rets.values())
    last = min(r.index[-1] for r in rets.values())
    y0 = first.year + (0 if (first.month, first.day) <= (1, 10) else 1)  # 第一个完整日历年
    segs: list[WfSegment] = []
    oos_parts: list[pd.Series] = []
    base_parts: list[pd.Series] = []
    for year in range(y0 + train_years, last.year + 1):
        lo, hi = pd.Timestamp(year - train_years, 1, 1), pd.Timestamp(year - 1, 12, 31)
        score = {k: _sharpe(r[(r.index >= lo) & (r.index <= hi)]) for k, r in rets.items()}
        chosen = max(sorted(score), key=lambda k: score[k])
        test, btest = _year(rets[chosen], year), _year(rets[base], year)
        if test.empty:
            continue
        segs.append(WfSegment(year, chosen, _ret(test), _ret(btest)))
        oos_parts.append(test)
        base_parts.append(btest)
    if not segs:
        return None
    return WalkForward(
        segs, (1 + pd.concat(oos_parts)).cumprod(), (1 + pd.concat(base_parts)).cumprod()
    )
