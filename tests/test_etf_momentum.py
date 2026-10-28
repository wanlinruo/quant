from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar, get_calendar
from quant.strategies.base import Context
from quant.strategies.etf_momentum import EtfMomentum, MomentumParams

SYMS = ["SPY", "QQQ", "TLT", "GLD", "SHY"]


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return get_calendar()


def trend_prices(cal: TradingCalendar, end: date, drifts: dict[str, float]) -> pd.DataFrame:
    """各标的按固定日收益率增长（无噪声），便于构造确定的排名。"""
    days = pd.DatetimeIndex(cal.trading_days(date(2024, 1, 2), end), name="date")
    n = np.arange(len(days))
    return pd.DataFrame({s: 100 * (1 + drifts.get(s, 0.0)) ** n for s in SYMS}, index=days)


def ctx_for(df: pd.DataFrame, as_of: date, **params: object) -> Context:
    def loader(symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
        return df.loc[: pd.Timestamp(end)].reindex(columns=list(symbols))

    return Context(as_of, loader, params)


BASE = {"SPY": 0.0005, "QQQ": 0.001, "TLT": 0.0002, "GLD": 0.0003, "SHY": 0.0001}
AS_OF = date(2026, 10, 30)  # 月末交易日


def test_picks_strongest(cal: TradingCalendar) -> None:
    df = trend_prices(cal, AS_OF, BASE)
    assert EtfMomentum(cal).run(ctx_for(df, AS_OF)) == {"QQQ": 1.0}


def test_top_k(cal: TradingCalendar) -> None:
    df = trend_prices(cal, AS_OF, BASE)
    w = EtfMomentum(cal).run(ctx_for(df, AS_OF, top_k=2))
    assert w == {"QQQ": 0.5, "SPY": 0.5}


def test_below_sma_goes_to_safe_asset(cal: TradingCalendar) -> None:
    df = trend_prices(cal, AS_OF, BASE)
    # QQQ 6 个月收益仍最高，但最近 20 天急跌到 200 日均线下方
    df.loc[df.index[-20:], "QQQ"] = df["QQQ"].iloc[-200:].mean() * 0.97
    df.loc[df.index[-127], "QQQ"] = df["QQQ"].iloc[-1] / 2  # 保证 126 日收益依然最高
    w = EtfMomentum(cal).run(ctx_for(df, AS_OF))
    assert w == {"SHY": 1.0}


def test_return_below_safe_asset_goes_to_safe(cal: TradingCalendar) -> None:
    drifts = {"SPY": -0.0005, "QQQ": -0.0002, "TLT": -0.001, "GLD": -0.0003, "SHY": 0.0001}
    df = trend_prices(cal, AS_OF, drifts)
    assert EtfMomentum(cal).run(ctx_for(df, AS_OF)) == {"SHY": 1.0}


def test_partial_filter_with_top_k(cal: TradingCalendar) -> None:
    drifts = {"SPY": -0.0001, "QQQ": 0.001, "TLT": -0.001, "GLD": -0.0003, "SHY": 0.0001}
    df = trend_prices(cal, AS_OF, drifts)
    assert EtfMomentum(cal).run(ctx_for(df, AS_OF, top_k=2)) == {"QQQ": 0.5, "SHY": 0.5}


def test_non_rebalance_day_uses_last_month_end(cal: TradingCalendar) -> None:
    df = trend_prices(cal, date(2026, 11, 20), BASE)
    # 10 月末之后 GLD 暴涨，但 11 月中旬不调仓
    after = df.index > pd.Timestamp(AS_OF)
    df.loc[after, "GLD"] = df.loc[after, "GLD"] * np.linspace(1.5, 3, after.sum())
    s = EtfMomentum(cal)
    assert s.rebalance_day(date(2026, 11, 20)) == AS_OF
    assert s.run(ctx_for(df, date(2026, 11, 20))) == {"QQQ": 1.0}


def test_stateless_and_deterministic(cal: TradingCalendar) -> None:
    df = trend_prices(cal, AS_OF, BASE)
    a, b = EtfMomentum(cal), EtfMomentum(cal)
    b.run(ctx_for(df, date(2026, 9, 30)))  # 先跑别的日期，不影响结果
    assert a.run(ctx_for(df, AS_OF)) == b.run(ctx_for(df, AS_OF))


def test_no_look_ahead(cal: TradingCalendar) -> None:
    """修改 as_of 之后的数据不得影响结果。"""
    df = trend_prices(cal, date(2026, 12, 31), BASE)
    base = EtfMomentum(cal).run(ctx_for(df, AS_OF))
    future = df.copy()
    later = future.index > pd.Timestamp(AS_OF)
    future.loc[later, "TLT"] = 10_000.0
    future.loc[later, "QQQ"] = 1.0
    assert EtfMomentum(cal).run(ctx_for(future, AS_OF)) == base


def test_symbol_with_short_history_skipped(cal: TradingCalendar) -> None:
    df = trend_prices(cal, AS_OF, {**BASE, "GLD": 0.01})
    df.loc[df.index[:-100], "GLD"] = np.nan  # 只有 100 天数据，不足 126
    assert EtfMomentum(cal).run(ctx_for(df, AS_OF)) == {"QQQ": 1.0}


def test_insufficient_safe_history_returns_cash(cal: TradingCalendar) -> None:
    df = trend_prices(cal, date(2024, 3, 28), BASE)  # 不足 126 天
    assert EtfMomentum(cal).run(ctx_for(df, date(2024, 3, 28))) == {}


def test_params_configurable_and_validated(cal: TradingCalendar) -> None:
    df = trend_prices(cal, AS_OF, BASE)
    w = EtfMomentum(cal).run(
        ctx_for(df, AS_OF, universe=["SPY", "TLT"], lookback=60, sma_window=50)
    )
    assert w == {"SPY": 1.0}
    with pytest.raises(ValueError, match="未知参数"):
        MomentumParams.from_mapping({"lookbak": 10})
    with pytest.raises(ValueError, match="top_k"):
        MomentumParams(top_k=5)
    with pytest.raises(ValueError, match="safe_asset"):
        MomentumParams(universe=("SPY", "SHY"))
