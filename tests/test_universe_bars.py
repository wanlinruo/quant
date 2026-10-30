"""候选池日线同步（#47 第二份计划任务 3）。假数据源，不访问网络。"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider, DataUnavailableError, normalize_bars
from quant.data.universe_bars import sync_universe_bars, universe_store

CAL = TradingCalendar()
START, END = date(2024, 1, 2), date(2024, 6, 28)
DAYS = CAL.trading_days(START, END)
SPLIT_DAY = date(2024, 6, 10)


class FakeSina(DataProvider):
    """未做拆股调整的日线；fail 中的代码拿不到；halved 中的代码在 SPLIT_DAY 价格腰斩（拆股）。"""

    source: ClassVar[str] = "sina"

    def __init__(self, fail: Sequence[str] = (), halved: Sequence[str] = ()) -> None:
        self.fail, self.halved = set(fail), set(halved)
        self.calls: list[str] = []

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append(symbol)
        if symbol in self.fail:
            raise DataUnavailableError(f"新浪没有 {symbol}")
        days = [d for d in DAYS if start <= d <= end]
        close = np.full(len(days), 100.0)
        if symbol in self.halved:
            close[[d >= SPLIT_DAY for d in days]] = 50.0
        idx = pd.DatetimeIndex(days)
        df = pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close,
             "volume": np.full(len(days), 1000, dtype="int64")},
            index=idx,
        )  # fmt: skip
        return normalize_bars(df, start, end)


def members(n: int) -> list[str]:
    return [f"S{i:03d}" for i in range(n)]


def no_splits(
    symbols: Sequence[str], start: date, end: date
) -> dict[str, list[tuple[date, float]]]:
    return {}


def test_tolerates_few_missing(tmp_path: Path) -> None:
    store = universe_store(tmp_path)
    m = members(100)
    r = sync_universe_bars(store, FakeSina(fail=m[:3]), no_splits, CAL, m, START, END)
    assert sorted(r.missing) == m[:3]
    assert r.coverage(100) == pytest.approx(0.97)
    assert len(store.symbols()) == 97


def test_fails_below_min_coverage(tmp_path: Path) -> None:
    m = members(100)
    with pytest.raises(DataValidationError):
        sync_universe_bars(universe_store(tmp_path), FakeSina(fail=m[:6]), no_splits, CAL, m,
                           START, END)  # fmt: skip


def test_split_adjusted_when_recorded(tmp_path: Path) -> None:
    store = universe_store(tmp_path)

    def splits(symbols: Sequence[str], s: date, e: date) -> dict[str, list[tuple[date, float]]]:
        return {"S000": [(SPLIT_DAY, 0.5)]}

    m = members(20)
    r = sync_universe_bars(store, FakeSina(halved=["S000"]), splits, CAL, m, START, END)
    assert "S000" not in r.excluded
    bars = store.read_bars("S000", START, END)
    assert bars["close"].iloc[0] == pytest.approx(50.0)  # 除权前 100 × 0.5
    assert bars["volume"].iloc[0] == 2000
    assert bars["adj_close"].iloc[0] == pytest.approx(50.0)


def test_unrecorded_split_kept_but_not_a_momentum_pick(tmp_path: Path) -> None:
    # 阈值 60% 后，未记录的 2 拆 1（-50%）不再被排除；它表现为暴跌，排序时自然靠后
    store = universe_store(tmp_path)
    m = members(40)
    r = sync_universe_bars(store, FakeSina(halved=["S001"]), no_splits, CAL, m, START, END)
    assert "S001" not in r.excluded and "S001" in store.symbols()


def test_skips_up_to_date(tmp_path: Path) -> None:
    store = universe_store(tmp_path)
    m = members(20)
    sync_universe_bars(store, FakeSina(), no_splits, CAL, m, START, END)
    sina = FakeSina()
    r = sync_universe_bars(store, sina, no_splits, CAL, m, START, END)
    assert sina.calls == [] and sorted(r.skipped) == m


class JumpSina(FakeSina):
    """S900 在 SPLIT_DAY 财报跳空 +52%（真实），S901 价格翻倍（未记录的 1 合 2）。"""

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = super().daily_bars(symbol, start, end)
        k = {"S900": 1.52, "S901": 2.0}.get(symbol)
        if k:
            m = df.index >= pd.Timestamp(SPLIT_DAY)
            for c in ("open", "high", "low", "close"):
                df.loc[m, c] = df.loc[m, c] * k
        return df


def test_threshold_matches_backtest(tmp_path: Path) -> None:
    # 与回测一致的 60%：真实的 +52% 财报跳空保留；+100%（未记录的合股）排除
    store = universe_store(tmp_path)
    m = [*members(40), "S900", "S901"]
    r = sync_universe_bars(store, JumpSina(), no_splits, CAL, m, START, END)
    assert "S900" not in r.excluded and "S900" in store.symbols()
    assert "S901" in r.excluded
