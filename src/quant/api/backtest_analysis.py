"""回测页的分析（#67 A 批）：由回测净值与 QQQ / SPY 价格在请求时计算。纯函数，只依赖 pandas。

口径与 engine/metrics.py 一致：252 个交易日年化，无风险利率按 0。
任何结果都不含 NaN / inf（转为 None）。
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
    ("sortino", "索提诺", "ratio"), ("max_drawdown", "最大回撤", "pct"),
    ("calmar", "卡玛", "ratio"),
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
    """全区间、样本内（2023 年以前）、样本外（2023 年起）的策略与 QQQ 指标；不足 2 天为 None。"""
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
    vals: dict[str, Any] = {k: (float("nan") if v is None else v) for k, v in d.items()}
    return Metrics(**vals)


def checks(split: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """组合策略验收标准（combo_checks 的 5 项）；缺数据的项与“相邻设置”（需稳健性检验）为 None。"""
    full, qfull = _from_dict(split["full"]["strategy"]), _from_dict(split["full"]["qqq"])
    ins, qins = _from_dict(split["ins"]["strategy"]), _from_dict(split["ins"]["qqq"])
    oos, qoos = _from_dict(split["oos"]["strategy"]), _from_dict(split["oos"]["qqq"])
    if full is None:
        return [{"label": lbl, "passed": None} for lbl in CHECK_LABELS]
    q = qfull or full
    res = combo_checks(full, q, ins or full, qins or q, oos or full, qoos or q, [])
    out: list[dict[str, Any]] = []
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
    for year, seg in eq.groupby(pd.DatetimeIndex(eq.index).year):
        base = prev if prev is not None else float(seg.iloc[0])
        out[int(str(year))] = float(seg.iloc[-1]) / base - 1
        prev = float(seg.iloc[-1])
    return out


def yearly(equity: pd.Series, qqq: pd.Series | None) -> list[dict[str, Any]]:
    eq = _clean(equity)
    if eq is None:
        return []
    q = align(eq, qqq) if qqq is not None else None
    ys, qs = _year_returns(eq), (_year_returns(q) if q is not None else {})
    return [{"year": y, "strategy": _f(v), "qqq": _f(qs.get(y))} for y, v in ys.items()]


# ---- 收益分析 ----

PERIOD_FREQ = {"周": "W-FRI", "月": "M", "季": "Q", "年": "Y"}


def period_returns_by(eq: pd.Series, freq: str) -> pd.Series:
    """按自然周期的收益：本期最后一个净值 / 上期最后一个净值 − 1（第一期以首日净值为基准）。

    索引为每期最后一个交易日。
    """
    idx = pd.DatetimeIndex(eq.index)
    grouped = eq.groupby(idx.to_period(freq))
    last = grouped.last()
    when = pd.DatetimeIndex(grouped.apply(lambda s: s.index[-1]))
    base = last.shift(1)
    base.iloc[0] = float(eq.iloc[0])
    return pd.Series((last / base - 1).to_numpy(), index=when)


def monthly_table(equity: pd.Series, qqq: pd.Series | None) -> list[dict[str, Any]]:
    eq = _clean(equity)
    if eq is None:
        return []
    m = period_returns_by(eq, "M")
    q = align(eq, qqq) if qqq is not None else None
    ys, qs = _year_returns(eq), (_year_returns(q) if q is not None else {})
    rows = []
    for y in sorted(ys):
        months: list[float | None] = [None] * 12
        sel = m[pd.DatetimeIndex(m.index).year == y]
        for d, v in zip(pd.DatetimeIndex(sel.index), sel.to_numpy(), strict=True):
            months[d.month - 1] = _f(v)
        rows.append(
            {"year": y, "months": months, "year_total": _f(ys[y]), "qqq_year": _f(qs.get(y))}
        )
    return rows


def distribution(equity: pd.Series, bins: int = 20) -> list[dict[str, Any]]:
    """月收益分布（等宽分组）。"""
    eq = _clean(equity)
    if eq is None:
        return []
    vals = [float(v) for v in period_returns_by(eq, "M").to_numpy() if math.isfinite(v)]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    width = (hi - lo) / bins if hi > lo else 0.0
    counts = [0] * bins
    for v in vals:
        i = min(int((v - lo) / width), bins - 1) if width else 0
        counts[i] += 1
    return [
        {"lo": lo + i * width, "hi": lo + (i + 1) * width, "count": c} for i, c in enumerate(counts)
    ]


def best_worst(equity: pd.Series) -> list[dict[str, Any]]:
    eq = _clean(equity)
    if eq is None:
        return []
    series = {"日": eq.pct_change().dropna()}
    series |= {k: period_returns_by(eq, f) for k, f in PERIOD_FREQ.items()}
    out = []
    for k, s in series.items():
        s = s[pd.Series(s).map(lambda v: math.isfinite(float(v))).to_numpy()]
        if s.empty:
            continue
        pos_hi, pos_lo = int(s.to_numpy().argmax()), int(s.to_numpy().argmin())
        idx = pd.DatetimeIndex(s.index)
        out.append({"period": k, "best": _f(s.iloc[pos_hi]), "best_at": str(idx[pos_hi].date()),
                    "worst": _f(s.iloc[pos_lo]), "worst_at": str(idx[pos_lo].date())})  # fmt: skip
    return out


def rolling_12m(equity: pd.Series) -> list[dict[str, Any]]:
    """滚动 12 个月收益与夏普，按月末取样。"""
    eq = _clean(equity)
    if eq is None:
        return []
    r = eq.pct_change()
    first = pd.Timestamp(eq.index[0])
    out = []
    for t in period_returns_by(eq, "M").index:
        start = pd.Timestamp(t) - pd.DateOffset(months=12)
        if start < first:
            continue
        base = eq[:start]
        win = r[(r.index > start) & (r.index <= t)].dropna()
        sd = win.std(ddof=1)
        sharpe = win.mean() * TRADING_DAYS / (sd * math.sqrt(TRADING_DAYS)) if sd else None
        out.append({"date": str(pd.Timestamp(t).date()), "ret": _f(eq[t] / base.iloc[-1] - 1),
                    "sharpe": _f(sharpe)})  # fmt: skip
    return out


# ---- 回撤与风险 ----


def drawdown_episodes(
    equity: pd.Series, qqq: pd.Series | None, top: int = 5
) -> list[dict[str, Any]]:
    """按“高点 → 谷底 → 收复”分段，取深度最大的 top 段（按深度排序）。

    天数按交易日；未收复的 end / recovery_days 为 None；qqq 为同期（高点到谷底）QQQ 收益。
    """
    eq = _clean(equity)
    if eq is None:
        return []
    q = align(eq, qqq) if qqq is not None else None
    vals = eq.to_numpy()
    idx = pd.DatetimeIndex(eq.index)
    eps: list[dict[str, Any]] = []
    peak_i = 0
    trough_i: int | None = None
    for i in range(1, len(vals) + 1):
        at_end = i == len(vals)
        if not at_end and vals[i] >= vals[peak_i]:
            if trough_i is not None:
                eps.append({"p": peak_i, "t": trough_i, "e": i})
            peak_i, trough_i = i, None
            continue
        if at_end:
            if trough_i is not None:
                eps.append({"p": peak_i, "t": trough_i, "e": None})
            break
        if trough_i is None or vals[i] < vals[trough_i]:
            trough_i = i
    out = []
    for e in sorted(eps, key=lambda e: vals[e["t"]] / vals[e["p"]])[:top]:
        p, t, end = e["p"], e["t"], e["e"]
        qret = _f(q.iloc[t] / q.iloc[p] - 1) if q is not None and len(q) == len(eq) else None
        out.append({
            "start": str(idx[p].date()), "trough": str(idx[t].date()),
            "end": str(idx[end].date()) if end is not None else None,
            "depth": _f(vals[t] / vals[p] - 1), "down_days": t - p,
            "recovery_days": end - t if end is not None else None, "qqq": qret,
        })  # fmt: skip
    return out


def _monthly_pair(eq: pd.Series, qqq: pd.Series | None) -> tuple[pd.Series, pd.Series] | None:
    q = align(eq, qqq) if qqq is not None else None
    if q is None:
        return None
    a, b = period_returns_by(eq, "M"), period_returns_by(q, "M")
    return a, b.reindex(a.index)


def capture(equity: pd.Series, qqq: pd.Series | None) -> dict[str, float | None]:
    """上 / 下行捕获率（按月）：QQQ 上涨（下跌）月份里，策略月收益均值 ÷ QQQ 月收益均值。"""
    eq = _clean(equity)
    pair = _monthly_pair(eq, qqq) if eq is not None else None
    if pair is None:
        return {"up": None, "down": None}
    s, q = pair
    up, dn = q > 0, q < 0
    return {
        "up": _f(s[up].mean() / q[up].mean()) if up.any() else None,
        "down": _f(s[dn].mean() / q[dn].mean()) if dn.any() else None,
    }


def _daily_pair(eq: pd.Series, qqq: pd.Series | None) -> tuple[pd.Series, pd.Series] | None:
    q = align(eq, qqq) if qqq is not None else None
    if q is None:
        return None
    df = pd.concat([eq.pct_change(), q.pct_change()], axis=1).dropna()
    return (df.iloc[:, 0], df.iloc[:, 1]) if len(df) > 2 else None


def risk_table(equity: pd.Series, qqq: pd.Series | None) -> list[dict[str, Any]]:
    eq = _clean(equity)
    rows: list[dict[str, Any]] = []
    m = _metrics(eq)
    mq = _metrics(align(eq, qqq)) if eq is not None and qqq is not None else None
    r = eq.pct_change().dropna() if eq is not None else pd.Series(dtype=float)
    neg = r[r < 0]
    down_vol = _f(neg.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(neg) > 1 else None

    def g(mm: Metrics | None, k: str) -> float | None:
        return _f(getattr(mm, k)) if mm else None

    rows += [
        {"label": "年波动", "value": g(m, "volatility"), "qqq": g(mq, "volatility")},
        {"label": "下行波动", "value": down_vol, "qqq": None},
        {"label": "最大回撤", "value": g(m, "max_drawdown"), "qqq": g(mq, "max_drawdown")},
        {"label": "卡玛", "value": g(m, "calmar"), "qqq": g(mq, "calmar")},
    ]
    pair = _daily_pair(eq, qqq) if eq is not None else None
    beta = corr = alpha = te = ir = None
    if pair is not None:
        s, q = pair
        var = q.var(ddof=1)
        beta = _f(s.cov(q) / var) if var else None
        corr = _f(s.corr(q))
        diff = s - q
        te = _f(diff.std(ddof=1) * math.sqrt(TRADING_DAYS))
        ir = _f(diff.mean() * TRADING_DAYS / te) if te else None
        if beta is not None:
            alpha = _f((s.mean() - beta * q.mean()) * TRADING_DAYS)
    rows += [
        {"label": "Beta", "value": beta, "qqq": None},
        {"label": "相关系数", "value": corr, "qqq": None},
        {"label": "年化 Alpha", "value": alpha, "qqq": None},
        {"label": "跟踪误差", "value": te, "qqq": None},
        {"label": "信息比率", "value": ir, "qqq": None},
    ]
    return rows


def rolling_beta(
    equity: pd.Series, qqq: pd.Series | None, window: int = 126
) -> list[dict[str, Any]]:
    """滚动 Beta（对 QQQ），按周取样（每周最后一个交易日）。"""
    eq = _clean(equity)
    pair = _daily_pair(eq, qqq) if eq is not None else None
    if pair is None or len(pair[0]) < window:
        return []
    s, q = pair
    beta = (s.rolling(window).cov(q) / q.rolling(window).var()).dropna()
    weekly = beta.groupby(pd.DatetimeIndex(beta.index).to_period("W-FRI")).last()
    when = beta.groupby(pd.DatetimeIndex(beta.index).to_period("W-FRI")).apply(
        lambda x: x.index[-1]
    )
    return [{"date": str(pd.Timestamp(d).date()), "beta": _f(v)}
            for d, v in zip(when.to_numpy(), weekly.to_numpy(), strict=True)]  # fmt: skip


def tail(equity: pd.Series) -> list[dict[str, Any]]:
    eq = _clean(equity)
    if eq is None:
        return []
    r = eq.pct_change().dropna()
    if len(r) < 2:
        return []
    var95 = r.quantile(0.05)
    weekly = period_returns_by(eq, "W-FRI")
    return [
        {"label": "VaR 95%（日）", "value": _f(var95)},
        {"label": "CVaR 95%（日）", "value": _f(r[r <= var95].mean())},
        {"label": "单日最大亏损", "value": _f(r.min())},
        {"label": "单周最大亏损", "value": _f(weekly.min())},
        {"label": "偏度", "value": _f(r.skew())},
        {"label": "峰度", "value": _f(r.kurt())},
    ]
