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


def test_not_ready_raises_retryable_and_keeps_history(tmp_path: Path) -> None:
    from quant.data.universe_bars import UniverseNotReady

    store = universe_store(tmp_path)
    m = members(20)
    prev = DAYS[-2]
    sync_universe_bars(store, FakeSina(), no_splits, CAL, m, START, prev)  # 前一交易日已同步

    # 当日数据还没出：新浪只返回到前一交易日
    class Late(FakeSina):
        def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            return super().daily_bars(symbol, start, prev)

    late = Late()
    with pytest.raises(UniverseNotReady):
        sync_universe_bars(store, late, no_splits, CAL, m, START, END)
    assert sorted(store.symbols()) == m  # 历史数据没有被删除


PREV = DAYS[-2]


class LateSina(FakeSina):
    """当日数据还没出：只返回到前一交易日。"""

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return super().daily_bars(symbol, start, PREV)


def today_bars(close: float = 100.0, only: Sequence[str] | None = None):  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []

    def fetch(symbols: Sequence[str], d: date) -> dict[str, pd.DataFrame]:
        calls.append(list(symbols))
        idx = pd.DatetimeIndex([pd.Timestamp(d)])
        df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                           "volume": 3000}, index=idx)  # fmt: skip
        return {s: normalize_bars(df, d, d) for s in symbols if only is None or s in only}

    return fetch, calls


def test_today_bar_fills_late_sina(tmp_path: Path) -> None:
    store = universe_store(tmp_path)
    m = members(20)
    fetch, calls = today_bars()
    r = sync_universe_bars(store, LateSina(), no_splits, CAL, m, START, END, today=fetch)
    assert sorted(r.fetched) == m and r.not_ready == []
    assert calls == [m]  # 一次批量查询
    bars = store.read_bars("S000", START, END)
    assert bars.index[-1].date() == END and bars["volume"].iloc[-1] == 3000
    assert len(bars) == len(DAYS)


def test_today_only_asked_for_late_symbols(tmp_path: Path) -> None:
    m = members(20)
    fetch, calls = today_bars()
    sync_universe_bars(universe_store(tmp_path), FakeSina(), no_splits, CAL, m, START, END,
                       today=fetch)  # fmt: skip
    assert calls == []  # 新浪已有当日数据：不查延迟行情


def test_today_missing_symbols_still_not_ready(tmp_path: Path) -> None:
    from quant.data.universe_bars import UniverseNotReady

    m = members(20)
    fetch, _ = today_bars(only=m[:10])
    with pytest.raises(UniverseNotReady):
        sync_universe_bars(universe_store(tmp_path), LateSina(), no_splits, CAL, m, START, END,
                           today=fetch)  # fmt: skip


def test_today_not_appended_over_gap(tmp_path: Path) -> None:
    # 新浪只到前两个交易日：中间缺一根，不能直接拼当日
    class Gap(FakeSina):
        def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            return super().daily_bars(symbol, start, DAYS[-3])

    store = universe_store(tmp_path)
    m = members(20)
    fetch, _ = today_bars()
    r = sync_universe_bars(store, Gap(), no_splits, CAL, m, START, END, today=fetch,
                           min_coverage=0.0)  # fmt: skip
    assert sorted(r.not_ready) == m


def test_today_bar_split_adjusted_with_history(tmp_path: Path) -> None:
    # 当日除权（1 拆 2）：延迟行情是拆后价 50，新浪历史是拆前价 100
    store = universe_store(tmp_path)

    def splits(symbols: Sequence[str], s: date, e: date) -> dict[str, list[tuple[date, float]]]:
        return {"S000": [(END, 0.5)]}

    fetch, _ = today_bars(close=50.0)
    r = sync_universe_bars(store, LateSina(), splits, CAL, members(20), START, END, today=fetch)
    assert "S000" in r.fetched
    bars = store.read_bars("S000", START, END)
    assert bars["close"].iloc[0] == pytest.approx(50.0)
    assert bars["close"].iloc[-1] == pytest.approx(50.0)


def test_today_source_error_counts_as_not_ready(tmp_path: Path) -> None:
    from quant.data.universe_bars import UniverseNotReady

    def broken(symbols: Sequence[str], d: date) -> dict[str, pd.DataFrame]:
        raise DataUnavailableError("老虎拉取延迟行情失败")

    with pytest.raises(UniverseNotReady):  # 可重试，而不是整体失败
        sync_universe_bars(universe_store(tmp_path), LateSina(), no_splits, CAL, members(20),
                           START, END, today=broken)  # fmt: skip


def test_coverage_error_lists_symbols(tmp_path: Path) -> None:
    # 错误信息直接列出缺失的代码，不用另查日志（#56）
    m = members(100)
    with pytest.raises(DataValidationError, match=r"缺失 6 只：S000, S001.*S005"):
        sync_universe_bars(universe_store(tmp_path), FakeSina(fail=m[:6]), no_splits, CAL, m,
                           START, END)  # fmt: skip
