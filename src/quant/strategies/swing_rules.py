"""波段策略的指标与进场判定（swing_pullback 设计第 5 节）。纯函数、向量化。

输入为单个标的按日期索引的 Series（含分红复权口径的开高低收、成交量）；
返回同索引的 bool Series。指标预热不足或数据缺失（NaN）的位置一律为 False。
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

ConfirmMode = Literal["close_strength", "volume", "rsi2"]

CLOSE_STRENGTH = 0.6  # 收盘位于当日振幅上部
WEAK_CLOSE = 0.4  # 收盘位于当日振幅下部
VOLUME_MULT = 1.5
VOLUME_WINDOW = 20
RSI_OVERSOLD = 10.0


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI（alpha = 1/n 的指数平滑）；第一个值为 NaN。"""
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-diff).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    out = (100 - 100 / (1 + gain / loss)).where(loss != 0, 100.0)  # 只涨不跌时为 100
    return out.where((gain != 0) | (loss != 0))  # 无涨跌（含首日）为 NaN


def _range_pos(h: pd.Series, lo: pd.Series, c: pd.Series) -> pd.Series:
    span = h - lo
    return ((c - lo) / span).where(span > 0, 0.5)


def _confirm(
    o: pd.Series,
    h: pd.Series,
    lo: pd.Series,
    c: pd.Series,
    v: pd.Series,
    mode: ConfirmMode,
) -> pd.Series:
    up = c > c.shift(1)
    if mode == "close_strength":
        return (_range_pos(h, lo, c) >= CLOSE_STRENGTH) & up
    if mode == "volume":
        avg = v.rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW).mean()
        return (v >= VOLUME_MULT * avg) & (c > o)
    if mode == "rsi2":
        return (rsi(c, 2).shift(1) < RSI_OVERSOLD) & up
    raise ValueError(f"未知确认方式：{mode}")


def long_setup(
    o: pd.Series,
    h: pd.Series,
    lo: pd.Series,
    c: pd.Series,
    v: pd.Series,
    *,
    trend_ma: int,
    slope_days: int,
    support_ma: int,
    tol: float,
    touch_days: int,
    confirm: ConfirmMode,
) -> pd.Series:
    """上升趋势中回踩支撑均线并出现止跌确认。"""
    trend = sma(c, trend_ma)
    up = c > trend
    if slope_days > 0:
        up &= trend > trend.shift(slope_days)
    support = sma(c, support_ma)
    touched = lo.rolling(touch_days, min_periods=touch_days).min() <= support * (1 + tol)
    holds = c >= support * (1 - tol)
    out = up & touched & holds & _confirm(o, h, lo, c, v, confirm)
    return out.fillna(False).astype(bool)


def short_setup(
    o: pd.Series,
    h: pd.Series,
    lo: pd.Series,
    c: pd.Series,
    *,
    trend_ma: int,
    support_ma: int,
    tol: float,
    touch_days: int,
) -> pd.Series:
    """下跌趋势中反抽到压制均线后受压回落（用于 QQQ → 买入 PSQ）。"""
    down = c < sma(c, trend_ma)
    resist = sma(c, support_ma)
    touched = h.rolling(touch_days, min_periods=touch_days).max() >= resist * (1 - tol)
    below = c < resist
    weak = (c < o) | (_range_pos(h, lo, c) <= WEAK_CLOSE)
    out = down & touched & below & weak
    return out.fillna(False).astype(bool)
