"""回测冒烟测试：使用固定随机种子生成的样本数据，不访问网络（进 CI）。"""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.broker.sim import CostModel
from quant.core.calendar import TradingCalendar, get_calendar
from quant.core.models import Side
from quant.engine.backtester import Backtester, adjusted_ohlc
from quant.strategies.base import Context, Strategy

START, END = date(2024, 1, 2), date(2025, 12, 31)
COSTS = CostModel(Decimal("0.005"), Decimal("1"), Decimal("0.0005"))  # 测试费率，非老虎实际费率


def sample_bars(cal: TradingCalendar, seed: int, drift: float, div_yield: float) -> pd.DataFrame:
    days = pd.DatetimeIndex(cal.trading_days(date(2023, 1, 3), END), name="date")
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, len(days))
    adj = 100 * np.cumprod(1 + rets)
    # close 只做拆股调整：比 adj_close 少了累计分红
    close = adj * np.exp(div_yield * (len(days) - 1 - np.arange(len(days))) / 252)
    opens = close * (1 + rng.normal(0, 0.002, len(days)))
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, close) * 1.005,
            "low": np.minimum(opens, close) * 0.995,
            "close": close,
            "adj_close": adj,
            "volume": np.full(len(days), 1_000_000, dtype="int64"),
        },
        index=days,
    )


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return get_calendar()


@pytest.fixture(scope="module")
def data(cal: TradingCalendar) -> dict[str, pd.DataFrame]:
    return {
        "SPY": sample_bars(cal, 1, 0.0004, 0.015),
        "TLT": sample_bars(cal, 2, 0.0001, 0.03),
        "SHY": sample_bars(cal, 3, 0.00005, 0.04),
    }


class Fixed(Strategy):
    name = "fixed"

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        self.seen: list[tuple[date, pd.Timestamp]] = []

    def target_weights(self, ctx: Context) -> dict[str, float]:
        self.seen.append((ctx.as_of, ctx.bars(["SPY"]).index.max()))
        return self.weights


class Alternating(Strategy):
    """每月末在 SPY 与 TLT 之间切换。"""

    name = "alternating"

    def __init__(self, cal: TradingCalendar) -> None:
        self.cal = cal
        self.current = "SPY"

    def target_weights(self, ctx: Context) -> dict[str, float]:
        if self.cal.is_month_end(ctx.as_of):
            self.current = "TLT" if self.current == "SPY" else "SPY"
        return {self.current: 1.0}


def run(strategy: Strategy, data: dict[str, pd.DataFrame], cal: TradingCalendar):  # type: ignore[no-untyped-def]
    return Backtester(strategy, data, cal, COSTS).run(START, END)


def test_cash_strategy_is_flat(data: dict[str, pd.DataFrame], cal: TradingCalendar) -> None:
    r = run(Fixed({}), data, cal)
    assert (r.equity == 100000).all()
    assert r.metrics.trades == 0
    assert r.metrics.total_return == 0


def test_buy_and_hold_tracks_benchmark(data: dict[str, pd.DataFrame], cal: TradingCalendar) -> None:
    r = run(Fixed({"SPY": 1.0}), data, cal)
    assert r.metrics.trades == 1
    assert r.fills[0].side is Side.BUY
    assert r.fills[0].filled_at.date() == cal.next_trading_day(START)  # 次日开盘成交
    assert r.benchmark is not None
    # 98% 仓位 + 成本，收益应接近但略低于基准
    assert r.metrics.total_return < r.benchmark.total_return
    assert r.metrics.total_return == pytest.approx(r.benchmark.total_return * 0.98, abs=0.02)
    assert r.weights["SPY"].iloc[-1] == pytest.approx(0.98, abs=0.01)


def test_dividends_included(data: dict[str, pd.DataFrame], cal: TradingCalendar) -> None:
    r = run(Fixed({"SHY": 1.0}), data, cal)
    shy = data["SHY"].loc[pd.Timestamp(START) : pd.Timestamp(END)]
    price_only = shy["close"].iloc[-1] / shy["close"].iloc[0] - 1
    total = shy["adj_close"].iloc[-1] / shy["adj_close"].iloc[0] - 1
    assert total > price_only + 0.05  # 样本数据中分红贡献约 8%
    assert r.metrics.total_return == pytest.approx(total * 0.98, abs=0.01)


def test_no_look_ahead(data: dict[str, pd.DataFrame], cal: TradingCalendar) -> None:
    s = Fixed({"SPY": 1.0})
    run(s, data, cal)
    assert len(s.seen) == len(cal.trading_days(START, END)) - 1
    assert all(seen == pd.Timestamp(as_of) for as_of, seen in s.seen)


def test_rotation_smoke(data: dict[str, pd.DataFrame], cal: TradingCalendar) -> None:
    r = run(Alternating(cal), data, cal)
    m = r.metrics
    months = len(cal.month_end_trading_days(START, END))
    # 首次买入 + 每次切换一卖一买（最后一个月末的信号来不及成交）
    assert m.trades == 1 + 2 * (months - 1)
    assert m.turnover > 5
    assert -1 < m.max_drawdown < 0
    assert all(np.isfinite([m.cagr, m.volatility, m.sharpe]))
    # 每天最多持有一个标的
    assert ((r.weights > 0).sum(axis=1) <= 1).all()
    assert r.equity.min() > 0


def test_adjusted_ohlc_requires_adj_close(data: dict[str, pd.DataFrame]) -> None:
    df = data["SPY"].copy()
    adj = adjusted_ohlc(df)
    assert (adj["close"] == df["adj_close"]).all()
    df.loc[df.index[0], "adj_close"] = np.nan
    with pytest.raises(ValueError, match="adj_close"):
        adjusted_ohlc(df)
