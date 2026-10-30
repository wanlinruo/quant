"""momentum_select 月度动量选股（#47 Task 4）。合成数据，不访问网络。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar
from quant.engine.backtester import field_loader_from_bars
from quant.strategies import get_strategy
from quant.strategies.base import Context
from quant.strategies.events import EventCalendar
from quant.strategies.momentum_select import (
    MomentumParams,
    MomentumSelect,
    rank_momentum,
    rebalance_day,
)
from quant.strategies.universe import Constituents

CAL = TradingCalendar()
DAYS = CAL.trading_days(date(2023, 4, 3), date(2024, 6, 28))
IDX = pd.DatetimeIndex(DAYS, name="date")
N = len(DAYS)
P = MomentumParams()
# 每日涨幅：A > B > C；D 最强但不是成员；E 收盘低于 5 元；F 涨得比 A 快但 2024-04 起缩量
GROWTH = {"A": 0.004, "B": 0.003, "C": 0.0015, "D": 0.005, "E": 0.0035, "F": 0.0045}
BASE = {"A": 50.0, "B": 50.0, "C": 50.0, "D": 50.0, "E": 1.5, "F": 50.0}


def make_close() -> pd.DataFrame:
    t = np.arange(N)
    return pd.DataFrame({s: BASE[s] * (1 + g) ** t for s, g in GROWTH.items()}, index=IDX)


def make_volume() -> pd.DataFrame:
    rising = np.linspace(1e6, 2e6, N)
    vol = pd.DataFrame({s: rising for s in GROWTH}, index=IDX)
    vol.loc[pd.Timestamp("2024-04-01") :, "F"] = 1e5  # 4 月起缩量：4、5、6 月末排序时都被过滤
    return vol


def test_rank_filters_and_order() -> None:
    assert rank_momentum(make_close(), make_volume(), {"A", "B", "C", "E", "F"}, P) == [
        "A",
        "B",
        "C",
    ]


def test_rank_liquidity_cut() -> None:
    vol = make_volume()
    vol["A"] = 1e3  # 成交额 A < B < C（B、C 价格相同时 B 涨得多，成交额更大……用成交量拉开）
    vol["B"] = vol["B"] * 3
    vol["C"] = vol["C"] * 4
    got = rank_momentum(make_close(), vol, {"A", "B", "C"}, replace(P, liquidity_top=2))
    assert got == ["B", "C"]


def test_rank_requires_history() -> None:
    close = make_close()
    close.loc[IDX[: N - 200], "B"] = np.nan  # B 只有 200 个交易日
    assert rank_momentum(close, make_volume(), {"A", "B", "C"}, P) == ["A", "C"]


def test_rebalance_day() -> None:
    assert rebalance_day(CAL, date(2024, 5, 31)) == date(2024, 5, 31)
    assert rebalance_day(CAL, date(2024, 6, 3)) == date(2024, 5, 31)
    assert rebalance_day(CAL, date(2024, 6, 28)) == date(2024, 6, 28)


def _bars(close: pd.DataFrame, volume: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        s: pd.DataFrame(
            {
                "open": close[s],
                "high": close[s] * 1.01,
                "low": close[s] * 0.99,
                "close": close[s],
                "adj_close": close[s],
                "volume": volume[s],
            }
        ).dropna()
        for s in close.columns
    }


def ctx(
    as_of: date,
    params: Mapping[str, object],
    close: pd.DataFrame | None = None,
    members: frozenset[str] = frozenset({"A", "B", "C", "E", "F"}),
    events: EventCalendar | None = None,
    universe: bool = True,
) -> Context:
    bars = _bars(make_close() if close is None else close, make_volume())
    return Context(
        as_of=as_of,
        loader=lambda s, a, b: pd.DataFrame(),
        params=params,
        field_loader=field_loader_from_bars(bars),
        events=events or EventCalendar(),
        universe=Constituents({date(2023, 1, 3): members}) if universe else None,
    )


def test_target_weights_equal_and_capped_by_top_n() -> None:
    s = MomentumSelect(CAL)
    assert s.run(ctx(date(2024, 6, 10), {"top_n": 2})) == pytest.approx({"A": 0.5, "B": 0.5})
    w = s.run(ctx(date(2024, 6, 10), {}))  # 只有 3 只合格
    assert w == pytest.approx({"A": 0.25, "B": 0.25, "C": 0.25})


def test_month_end_uses_new_ranking() -> None:
    close = make_close()
    close.loc[pd.Timestamp("2024-05-31") :, "C"] *= 2  # 5-31 C 跳涨，成为最强
    s = MomentumSelect(CAL)
    assert s.run(ctx(date(2024, 5, 30), {"top_n": 1}, close)) == {"A": 1.0}
    assert s.run(ctx(date(2024, 5, 31), {"top_n": 1}, close)) == {"C": 1.0}


def test_earnings_exit() -> None:
    june = CAL.trading_days(date(2024, 6, 1), date(2024, 6, 28))
    events = EventCalendar(earnings={"A": (june[9],)})  # 6 月第 10 个交易日为反应日
    hold = MomentumSelect(CAL)
    for d in june:
        assert "A" in hold.run(ctx(d, {"top_n": 2}, events=events))
    exit_ = MomentumSelect(CAL)
    params = {"top_n": 2, "earnings_exit": True}
    for i, d in enumerate(june[:-1]):
        w = exit_.run(ctx(d, params, events=events))
        assert ("A" in w) == (i < 7), d  # 第 8 个交易日起卖出，当月不补回
        assert w["B"] == pytest.approx(0.5)
    # 6-28 是月末：按新的排名，A 已过财报，重新入选
    assert "A" in exit_.run(ctx(june[-1], params, events=events))


def test_requires_universe() -> None:
    with pytest.raises(ValueError, match="成分股"):
        MomentumSelect(CAL).run(ctx(date(2024, 6, 10), {}, universe=False))


def test_params_reject_unknown() -> None:
    with pytest.raises(ValueError, match="未知参数"):
        MomentumParams.from_mapping({"topn": 3})


def test_registered() -> None:
    s = get_strategy("momentum_select")
    assert s.symbols({}) == []
    assert s.warmup_days({}) == 252 + 30
