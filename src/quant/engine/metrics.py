"""回测指标（PROJECT_PLAN 第 9 章）。无风险利率按 0 计。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class Metrics:
    start: str
    end: str
    total_return: float
    cagr: float
    volatility: float
    max_drawdown: float  # 负数，如 -0.25
    sharpe: float
    calmar: float
    turnover: float  # 年化单边换手：年均成交额 / 2 / 平均净值
    trades: int

    def to_dict(self) -> dict[str, float | int | str | None]:
        """可写入 JSON：NaN / inf 转为 None。"""
        out: dict[str, float | int | str | None] = {}
        for k, v in asdict(self).items():
            out[k] = None if isinstance(v, float) and not math.isfinite(v) else v
        return out


def drawdown(equity: pd.Series) -> pd.Series:
    """每日回撤（≤ 0）。"""
    peak = equity.cummax()
    return equity / peak - 1


def compute_metrics(equity: pd.Series, traded_notional: float = 0.0, trades: int = 0) -> Metrics:
    """equity：按交易日的净值序列（日期索引）。"""
    eq = equity.dropna().astype(float)
    if len(eq) < 2 or eq.iloc[0] <= 0:
        raise ValueError("净值序列至少需要 2 个正值")
    rets = eq.pct_change().dropna()
    years = len(rets) / TRADING_DAYS
    total = float(eq.iloc[-1] / eq.iloc[0] - 1)
    cagr = float((1 + total) ** (1 / years) - 1) if years > 0 else float("nan")
    vol = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(rets) > 1 else float("nan")
    mean = float(rets.mean() * TRADING_DAYS)
    sharpe = mean / vol if vol and math.isfinite(vol) and vol > 0 else float("nan")
    mdd = float(drawdown(eq).min())
    calmar = cagr / abs(mdd) if mdd < 0 else float("nan")
    turnover = traded_notional / 2 / float(eq.mean()) / years if years > 0 else float("nan")
    idx = pd.DatetimeIndex(eq.index)
    return Metrics(
        start=str(idx[0].date()),
        end=str(idx[-1].date()),
        total_return=total,
        cagr=cagr,
        volatility=vol,
        max_drawdown=mdd,
        sharpe=float(sharpe),
        calmar=float(calmar),
        turnover=float(turnover),
        trades=trades,
    )


def buy_and_hold(prices: pd.Series, initial: float = 1.0) -> pd.Series:
    """基准：首日买入持有（用含分红复权价）。"""
    p = prices.dropna().astype(float)
    return p / p.iloc[0] * initial if len(p) else p.astype(np.float64)


def equal_weight_rebalanced(prices: pd.DataFrame, initial: float = 1.0) -> pd.Series:
    """基准：等权持有一组标的，每月最后一个交易日收盘再平衡，不计成本。"""
    px = prices.dropna(how="all").astype(float)
    rets = px.pct_change(fill_method=None).fillna(0.0)
    month = pd.Series(pd.DatetimeIndex(px.index).to_period("M"), index=px.index)
    month_end = month != month.shift(-1)
    values = []
    weights = np.full(px.shape[1], 1.0 / px.shape[1])
    nav = initial
    for i, ts in enumerate(px.index):
        if i > 0:
            growth = weights * (1 + rets.iloc[i].to_numpy())
            nav *= growth.sum()
            weights = growth / growth.sum()
        values.append(nav)
        if month_end.loc[ts]:
            weights = np.full(px.shape[1], 1.0 / px.shape[1])
    return pd.Series(values, index=px.index, name="equity")
