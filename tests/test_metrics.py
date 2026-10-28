import math

import numpy as np
import pandas as pd
import pytest

from quant.engine.metrics import TRADING_DAYS, buy_and_hold, compute_metrics, drawdown


def series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2026-01-01", periods=len(values)))


def test_constant_growth() -> None:
    # 每日 +0.1%，一年 252 天
    eq = series(list(1.001 ** np.arange(TRADING_DAYS + 1)))
    m = compute_metrics(eq)
    assert m.total_return == pytest.approx(1.001**TRADING_DAYS - 1)
    assert m.cagr == pytest.approx(m.total_return)
    assert m.volatility == pytest.approx(0, abs=1e-9)
    assert m.max_drawdown == 0
    assert math.isnan(m.calmar)
    assert m.to_dict()["calmar"] is None


def test_drawdown_and_calmar() -> None:
    eq = series([100, 120, 90, 110, 130])
    dd = drawdown(eq)
    assert dd.min() == pytest.approx(90 / 120 - 1)
    m = compute_metrics(eq)
    assert m.max_drawdown == pytest.approx(-0.25)
    assert m.calmar == pytest.approx(m.cagr / 0.25)


def test_sharpe_and_volatility() -> None:
    rets = np.array([0.01, -0.005] * 126)
    eq = series(list(100 * np.cumprod(np.concatenate([[1.0], 1 + rets]))))
    m = compute_metrics(eq)
    vol = rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
    assert m.volatility == pytest.approx(vol)
    assert m.sharpe == pytest.approx(rets.mean() * TRADING_DAYS / vol)


def test_turnover_and_trades() -> None:
    eq = series([100.0] * (TRADING_DAYS + 1))
    # 一年内买卖总额 400 = 单边 200 = 平均净值的 2 倍
    m = compute_metrics(eq, traded_notional=400, trades=4)
    assert m.turnover == pytest.approx(2.0)
    assert m.trades == 4


def test_buy_and_hold() -> None:
    bh = buy_and_hold(series([50, 55, 60]), 1000)
    assert bh.tolist() == [1000, 1100, 1200]


def test_too_short() -> None:
    with pytest.raises(ValueError):
        compute_metrics(series([100]))
