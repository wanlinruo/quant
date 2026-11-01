"""回测页的分析（#67 A 批）：由回测净值与 QQQ / SPY 价格在请求时计算。纯函数，只依赖 pandas。

口径与 engine/metrics.py 一致：252 个交易日年化，无风险利率按 0。任何结果都不含 NaN / inf（转为 None）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from quant.engine.evaluation import OOS_START, combo_checks
from quant.engine.metrics import TRADING_DAYS, Metrics, compute_metrics

Kind = Literal["pct", "ratio", "int"]


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clean(eq: pd.Series | None) -> pd.Series | None:
    if eq is None:
        return None
    s = eq.dropna().astype(float)
    s = s[s > 0]
    return s if len(s) >= 2 else None


def align(eq: pd.Series, other: pd.Series | None) -> pd.Series | None:
    """把基准价格对齐到策略净值的交易日（缺失向前填充），截取同一区间。"""
    if other is None or other.empty:
        return None
    o = other.dropna().astype(float).sort_index()
    o = o.reindex(o.index.union(eq.index)).ffill().reindex(eq.index)
    return _clean(o)


@dataclass(frozen=True)
class Stat:
    key: str
    label: str
    value: float | None
    qqq: float | None
    kind: Kind


def sortino(equity: pd.Series) -> float | None:
    eq = _clean(equity)
    if eq is None:
        return None
    r = eq.pct_change().dropna()
    neg = r[r < 0]
    if len(neg) < 2:
        return None
    years = len(r) / TRADING_DAYS
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    down = neg.std(ddof=1) * math.sqrt(TRADING_DAYS)
    return _f(cagr / down) if down else None


def _metrics(eq: pd.Series | None) -> Metrics | None:
    eq = _clean(eq)
    if eq is None:
        return None
    try:
        return compute_metrics(eq)
    except ValueError:
        return None


STATS: tuple[tuple[str, str, Kind], ...] = (
    ("total_return", "总收益", "pct"), ("cagr", "年化", "pct"), ("sharpe", "夏普", "ratio"),
    ("sortino", "索提诺", "ratio"), ("max_drawdown", "最大回撤", "pct"), ("calmar", "卡玛", "ratio"),
    ("volatility", "年波动", "pct"), ("win_rate", "胜率", "pct"),
    ("profit_factor", "盈亏比", "ratio"), ("trades", "交易笔数", "int"),
)  # fmt: skip
NEEDS_TRADES = {"win_rate", "profit_factor", "trades"}  # 交易口径（设计 7.1）在 B 批接入


def stat_bar(equity: pd.Series, qqq: pd.Series | None) -> list[Stat]:
    m, mq = _metrics(equity), _metrics(align(equity, qqq) if qqq is not None else None)

    def val(mm: Metrics | None, key: str, eq: pd.Series | None) -> float | None:
        if mm is None or key in NEEDS_TRADES:
            return None
        if key == "sortino":
            return sortino(eq) if eq is not None else None
        return _f(getattr(mm, key))

    q_eq = align(equity, qqq) if qqq is not None else None
    return [Stat(k, label, val(m, k, equity), val(mq, k, q_eq), kind) for k, label, kind in STATS]


def _segments(eq: pd.Series) -> dict[str, pd.Series]:
    cut = pd.Timestamp(OOS_START)
    return {"full": eq, "ins": eq[eq.index < cut], "oos": eq[eq.index >= cut]}


def split_metrics(equity: pd.Series, qqq: pd.Series | None) -> dict[str, dict[str, Any]]:
    """全区间、样本内（2023 年以前）、样本外（2023 年起）的策略与 QQQ 指标；某段不足 2 天为 None。"""
    q = align(equity, qqq) if qqq is not None else None
    segs, qsegs = _segments(equity), (_segments(q) if q is not None else {})
    out: dict[str, dict[str, Any]] = {}
    for k, seg in segs.items():
        ms, mq = _metrics(seg), _metrics(qsegs.get(k))
        out[k] = {"strategy": ms.to_dict() if ms else None, "qqq": mq.to_dict() if mq else None}
    return out


def _from_dict(d: dict[str, Any] | None) -> Metrics | None:
    if not d:
        return None
    return Metrics(**{k: (float("nan") if v is None else v) for k, v in d.items()})


def checks(split: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """组合策略验收标准（combo_checks 的 5 项）；缺数据的项与“相邻设置”（需稳健性检验）为 None。"""
    full, qfull = _from_dict(split["full"]["strategy"]), _from_dict(split["full"]["qqq"])
    ins, qins = _from_dict(split["ins"]["strategy"]), _from_dict(split["ins"]["qqq"])
    oos, qoos = _from_dict(split["oos"]["strategy"]), _from_dict(split["oos"]["qqq"])
    if full is None:
        return [{"label": lbl, "passed": None} for lbl in CHECK_LABELS]
    q = qfull or full
    res = combo_checks(full, q, ins or full, qins or q, oos or full, qoos or q, [])
    out = []
    for i, (label, passed) in enumerate(res.items()):
        missing = (i in (1, 3) and qfull is None) or (i == 3 and (ins is None or oos is None))
        if missing or i == 4:
            out.append({"label": label, "passed": None})
        else:
            out.append({"label": label, "passed": bool(passed)})
    return out


CHECK_LABELS = (
    "最大回撤 ≥ -25%", "年化 ≥ QQQ + 3 个百分点", "夏普 ≥ 1.1", "两个区间夏普都高于 QQQ",
    "相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0",
)  # fmt: skip


def _year_returns(eq: pd.Series) -> dict[int, float]:
    out: dict[int, float] = {}
    prev: float | None = None
    for year, seg in eq.groupby(eq.index.year):
        base = prev if prev is not None else float(seg.iloc[0])
        out[int(year)] = float(seg.iloc[-1]) / base - 1
        prev = float(seg.iloc[-1])
    return out


def yearly(equity: pd.Series, qqq: pd.Series | None) -> list[dict[str, Any]]:
    eq = _clean(equity)
    if eq is None:
        return []
    q = align(eq, qqq) if qqq is not None else None
    ys, qs = _year_returns(eq), (_year_returns(q) if q is not None else {})
    return [{"year": y, "strategy": _f(v), "qqq": _f(qs.get(y))} for y, v in ys.items()]
