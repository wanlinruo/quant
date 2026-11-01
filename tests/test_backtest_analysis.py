"""回测分析纯函数（#67 A 批）。合成净值，不访问数据库与网络。"""

import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.api import backtest_analysis as ba
from quant.engine.evaluation import OOS_START


def series(
    start: str, n: int, seed: int = 1, drift: float = 0.0005, vol: float = 0.01
) -> pd.Series:
    idx = pd.bdate_range(start, periods=n)
    r = np.random.default_rng(seed).normal(drift, vol, n)
    r[0] = 0.0
    return pd.Series(100 * np.cumprod(1 + r), index=idx)


EQ = series("2019-01-01", 1800, 1)
QQQ = series("2019-01-01", 1800, 2, drift=0.0004)


def dumps(x: object) -> str:
    return json.dumps(x, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o),
                      allow_nan=False)  # fmt: skip


def test_oos_start() -> None:
    assert date(2023, 1, 1) == OOS_START


def test_sortino_standard_definition() -> None:
    """下行偏差以 0 为目标、对全部样本：sqrt(mean(min(r,0)^2))；分子与夏普同为算术均值年化。"""
    r = EQ.pct_change().dropna()
    dd = math.sqrt(float((r.clip(upper=0) ** 2).mean())) * math.sqrt(252)
    assert ba.sortino(EQ) == pytest.approx(r.mean() * 252 / dd)
    rows = {x["label"]: x for x in ba.risk_table(EQ, None)}
    assert rows["下行波动"]["value"] == pytest.approx(dd)


def test_stat_bar() -> None:
    st = ba.stat_bar(EQ, QQQ)
    keys = ["total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "volatility",
            "win_rate", "profit_factor", "trades"]  # fmt: skip
    assert [s.key for s in st] == keys
    assert st[0].value == pytest.approx(EQ.iloc[-1] / EQ.iloc[0] - 1)
    assert st[0].qqq == pytest.approx(QQQ.iloc[-1] / QQQ.iloc[0] - 1)
    assert all(s.value is None and s.qqq is None for s in st[-3:])
    assert all(s.qqq is None for s in ba.stat_bar(EQ, None))


def test_split_metrics_and_checks() -> None:
    sp = ba.split_metrics(EQ, QQQ)  # 2019-01 ~ 2025-11，跨 2023
    assert sp["full"]["strategy"] and sp["ins"]["strategy"] and sp["oos"]["strategy"]
    assert (
        sp["oos"]["strategy"]["start"] >= "2023-01-01"
        and sp["ins"]["strategy"]["end"] < "2023-01-01"
    )
    ch = ba.checks(sp)
    assert len(ch) == 5 and ch[4]["passed"] is None and isinstance(ch[0]["passed"], bool)
    short = ba.split_metrics(EQ[:"2020-12-31"], QQQ[:"2020-12-31"])
    assert short["oos"]["strategy"] is None
    assert ba.checks(short)[3]["passed"] is None


def test_yearly() -> None:
    ys = ba.yearly(EQ, QQQ)
    assert ys[0]["year"] == 2019
    y19 = EQ["2019"]
    assert ys[0]["strategy"] == pytest.approx(y19.iloc[-1] / y19.iloc[0] - 1)
    y20 = EQ[:"2020-12-31"]
    assert ys[1]["strategy"] == pytest.approx(y20.iloc[-1] / EQ["2019"].iloc[-1] - 1)
    assert ys[1]["qqq"] is not None


def test_tiny_series_safe() -> None:
    tiny = EQ.iloc[:5]
    sp = ba.split_metrics(tiny, None)
    out = [ba.stat_bar(tiny, None), sp, ba.checks(sp), ba.yearly(tiny, None)]
    dumps(out)


# ---- Task 2：收益分析 ----


def monthly_series() -> pd.Series:
    """每月最后一个交易日收益已知：1 月 +10%，2 月 -5%，其余 0；从 2021-01 起两年。"""
    idx = pd.bdate_range("2020-12-31", "2022-12-30")
    level = 100.0
    vals = []
    for d in idx:
        if d.month == 1 and d == idx[(idx.year == d.year) & (idx.month == 1)][-1]:
            level *= 1.10
        if d.month == 2 and d == idx[(idx.year == d.year) & (idx.month == 2)][-1]:
            level *= 0.95
        vals.append(level)
    return pd.Series(vals, index=idx)


def test_monthly_table() -> None:
    eq = monthly_series()
    rows = ba.monthly_table(eq, eq)
    r21 = next(r for r in rows if r["year"] == 2021)
    assert r21["months"][0] == pytest.approx(0.10) and r21["months"][1] == pytest.approx(-0.05)
    assert r21["months"][2] == pytest.approx(0.0)
    assert r21["year_total"] == pytest.approx(1.10 * 0.95 - 1) and r21["qqq_year"] == pytest.approx(
        r21["year_total"]
    )
    r20 = next(r for r in rows if r["year"] == 2020)
    assert r20["months"][:11] == [None] * 11  # 只有 12-31 一天


def test_distribution_counts_months() -> None:
    eq = monthly_series()
    bins = ba.distribution(eq, bins=5)
    n_months = sum(1 for r in ba.monthly_table(eq, None) for m in r["months"] if m is not None)
    assert sum(b["count"] for b in bins) == n_months and len(bins) == 5


def test_best_worst() -> None:
    eq = monthly_series()
    bw = {r["period"]: r for r in ba.best_worst(eq)}
    assert set(bw) == {"日", "周", "月", "季", "年"}
    assert bw["月"]["best"] == pytest.approx(0.10) and bw["月"]["best_at"].startswith("2021-01")
    assert bw["月"]["worst"] == pytest.approx(-0.05) and bw["月"]["worst_at"][5:7] == "02"


def test_rolling_12m() -> None:
    roll = ba.rolling_12m(EQ)
    assert roll[0]["date"][:7] == "2019-12" or roll[0]["date"][:7] == "2020-01"
    first = pd.Timestamp(roll[0]["date"])
    base = EQ[: first - pd.DateOffset(months=12)].iloc[-1]
    assert roll[0]["ret"] == pytest.approx(EQ[:first].iloc[-1] / base - 1)
    assert ba.rolling_12m(EQ.iloc[:30]) == []


# ---- Task 3：回撤与风险 ----


def two_drawdowns() -> pd.Series:
    """100 → 120（新高）→ 96（-20%）→ 125（收复并创新高）→ 112.5（-10%，未收复）。"""
    pts = [100, 110, 120, 108, 96, 110, 121, 125, 118, 112.5, 115]
    return pd.Series(pts, index=pd.bdate_range("2024-01-01", periods=len(pts)), dtype=float)


def test_drawdown_episodes() -> None:
    eq = two_drawdowns()
    eps = ba.drawdown_episodes(eq, None)
    assert len(eps) == 2
    a, b = eps
    assert (
        a["depth"] == pytest.approx(-0.2)
        and a["start"] == "2024-01-03"
        and a["trough"] == "2024-01-05"
    )
    assert a["end"] == "2024-01-09" and a["down_days"] == 2 and a["recovery_days"] == 2
    assert b["depth"] == pytest.approx(-0.1) and b["end"] is None and b["recovery_days"] is None
    assert a["qqq"] is None
    with_q = ba.drawdown_episodes(eq, eq)
    assert with_q[0]["qqq"] == pytest.approx(-0.2)  # 同期 = 策略从高点到谷底的这段时间


def test_capture_and_risk_vs_self() -> None:
    cap = ba.capture(EQ, EQ)
    assert cap["up"] == pytest.approx(1.0) and cap["down"] == pytest.approx(1.0)
    rows = {r["label"]: r for r in ba.risk_table(EQ, EQ)}
    assert rows["Beta"]["value"] == pytest.approx(1.0) and rows["相关系数"][
        "value"
    ] == pytest.approx(1.0)
    assert ba.capture(EQ, None) == {"up": None, "down": None}
    assert {r["label"]: r for r in ba.risk_table(EQ, None)}["Beta"]["value"] is None


def test_rolling_beta_and_tail() -> None:
    rb = ba.rolling_beta(EQ, EQ)
    assert rb and all(x["beta"] == pytest.approx(1.0) for x in rb[:5])
    assert ba.rolling_beta(EQ.iloc[:50], EQ.iloc[:50]) == []
    tail = {r["label"]: r["value"] for r in ba.tail(EQ)}
    r = EQ.pct_change().dropna()
    assert tail["VaR 95%（日）"] == pytest.approx(r.quantile(0.05))
    assert tail["单日最大亏损"] == pytest.approx(r.min())
    assert tail["CVaR 95%（日）"] <= tail["VaR 95%（日）"]
    dumps(
        [
            ba.drawdown_episodes(EQ.iloc[:3], None),
            ba.tail(EQ.iloc[:3]),
            ba.risk_table(EQ.iloc[:3], None),
        ]
    )


# ---- 终审修复：基准覆盖 ----


def test_covers() -> None:
    assert ba.covers(EQ, QQQ)
    assert not ba.covers(EQ, QQQ["2021":])  # 晚开始
    assert not ba.covers(EQ, QQQ[:"2024-06-30"])  # 早结束
    assert not ba.covers(EQ, None)


def test_align_does_not_extend_past_benchmark_end() -> None:
    q = ba.align(EQ, QQQ[:"2024-06-30"])
    assert q is not None and q.index[-1] <= pd.Timestamp("2024-06-30")


def test_episode_qqq_by_date_with_late_benchmark() -> None:
    eq = two_drawdowns()
    q = eq.iloc[2:] * 2  # 从第一个高点当天起才有数据
    eps = ba.drawdown_episodes(eq, q)
    assert eps[0]["qqq"] == pytest.approx(-0.2)


def test_checks_none_when_benchmark_segment_missing() -> None:
    sp = ba.split_metrics(EQ, QQQ["2023":])  # QQQ 只有样本外
    assert ba.checks(sp)[3]["passed"] is None
