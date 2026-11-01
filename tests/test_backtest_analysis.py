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


def test_sortino_matches_manual() -> None:
    r = EQ.pct_change().dropna()
    years = len(r) / 252
    cagr = (EQ.iloc[-1] / EQ.iloc[0]) ** (1 / years) - 1
    down = r[r < 0].std(ddof=1) * math.sqrt(252)
    assert ba.sortino(EQ) == pytest.approx(cagr / down)


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
