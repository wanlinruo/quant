"""波段进出场判定（swing_pullback Task 4）。用手工构造的 260 日序列。"""

import numpy as np
import pandas as pd
import pytest

from quant.strategies.swing_rules import long_setup, rsi, short_setup, sma

N = 260
T = 250  # 触发日
IDX = pd.bdate_range("2023-01-02", periods=N)
KW = {"trend_ma": 120, "slope_days": 0, "support_ma": 20, "tol": 0.01, "touch_days": 3}


def _frame(growth: float) -> dict[str, pd.Series]:
    c = pd.Series(100 * growth ** np.arange(N), index=IDX)
    return {"o": c.shift(1).fillna(c.iloc[0]), "h": c * 1.01, "l": c * 0.99, "c": c,
            "v": pd.Series(1000.0, index=IDX)}  # fmt: skip


def _pullback(confirm_close_up: bool = True) -> dict[str, pd.Series]:
    """上升趋势中，T 之前 4 日回调，T 日最低触及 20 日线后收阳。"""
    f = _frame(1.004)
    c = f["c"].copy()
    for t in range(T - 4, T):
        c.iloc[t] = c.iloc[t - 1] * 0.99
    c.iloc[T] = c.iloc[T - 1] * (1.01 if confirm_close_up else 0.995)
    c.iloc[T + 1 :] = c.iloc[T] * 1.004 ** np.arange(1, N - T)
    f["c"] = c
    f["o"] = c.shift(1).fillna(c.iloc[0])
    f["h"] = c * 1.01
    f["l"] = c * 0.99
    s20 = sma(c, 20)
    # 触及支撑：最低价低于 20 日线（也低于收盘价，K 线合法）
    f["l"].iloc[T] = min(c.iloc[T], s20.iloc[T]) * 0.985
    f["h"].iloc[T] = c.iloc[T] * 1.001  # 收在当日高位
    return f


def _long(f: dict[str, pd.Series], confirm: str = "close_strength") -> pd.Series:
    return long_setup(f["o"], f["h"], f["l"], f["c"], f["v"], confirm=confirm, **KW)  # type: ignore[arg-type]


def test_long_setup_triggers_on_pullback() -> None:
    s = _long(_pullback())
    assert s.iloc[T]
    assert not s.iloc[:T].any()


def test_long_setup_requires_uptrend() -> None:
    f = _frame(0.997)
    f["l"] = sma(f["c"], 20) * 0.999  # 每天都“触及”，但在 120 日线下方
    assert not _long(f).any()


def test_confirm_close_strength() -> None:
    assert _long(_pullback(confirm_close_up=True)).iloc[T]
    assert not _long(_pullback(confirm_close_up=False)).iloc[T]


def test_confirm_volume() -> None:
    f = _pullback()
    assert not _long(f, "volume").iloc[T]  # 没有放量
    f["v"].iloc[T] = 2000.0
    assert _long(f, "volume").iloc[T]


def test_confirm_rsi2() -> None:
    assert _long(_pullback(), "rsi2").iloc[T]  # 连跌 4 日后 RSI(2) 接近 0，当日收涨
    f = _pullback()
    for t in range(T - 4, T):  # 回调日改为小幅上涨：RSI(2) 不超卖
        f["c"].iloc[t] = f["c"].iloc[t - 1] * 1.0001
    assert not _long(f, "rsi2").iloc[T]


def test_short_setup_rejection() -> None:
    f = _frame(0.996)
    c = f["c"]
    s20 = sma(c, 20)
    f["h"].iloc[T] = s20.iloc[T] * 1.0  # 反抽触及 20 日线
    f["o"].iloc[T] = c.iloc[T] * 1.01  # 收阴
    s = short_setup(f["o"], f["h"], f["l"], c, trend_ma=120, support_ma=20, tol=0.01,
                    touch_days=3)  # fmt: skip
    assert s.iloc[T]
    assert not s.iloc[:T].any()


def test_warmup_false_and_nan_safe() -> None:
    f = _pullback()
    assert not _long(f).iloc[:119].any()
    for k in f:
        f[k] = f[k].copy()
        f[k].iloc[200] = np.nan
    s = _long(f)
    assert not s.iloc[200]
    assert s.dtype == bool


def test_rsi_known_values() -> None:
    assert rsi(pd.Series([10.0, 11.0, 10.0]), 2).iloc[2] == pytest.approx(50.0, abs=1e-9)
    up = rsi(pd.Series(np.arange(1.0, 20.0)), 2).dropna()
    down = rsi(pd.Series(np.arange(20.0, 1.0, -1)), 2).dropna()
    assert (up == 100).all() and (down == 0).all()
