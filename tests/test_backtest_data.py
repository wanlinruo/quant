"""服务器回测数据补齐（#66 Task 2）。不访问网络。"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import get_calendar
from quant.core.settings import TiingoSettings
from quant.data import backtest_data as bd
from quant.data.providers.base import DataProvider, DataUnavailableError
from quant.data.quota import QuotaExceeded, QuotaLedger
from quant.data.store import ParquetStore
from quant.strategies.universe import Constituents

TODAY = date(2026, 10, 30)
CAL = get_calendar()


def sample(end: date = TODAY, start: date = date(2015, 1, 2)) -> pd.DataFrame:
    days = pd.DatetimeIndex(CAL.trading_days(start, end), name="date")
    close = 100 + np.arange(len(days)) * 0.05
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "adj_close": close,
         "volume": np.full(len(days), 1000, dtype="int64")},
        index=days,
    )  # fmt: skip


class FakeProvider(DataProvider):
    source = "tiingo"

    def __init__(
        self, ledger: QuotaLedger, missing: frozenset[str] = frozenset(),
        bad: frozenset[str] = frozenset(), quota: bool = False,
    ) -> None:  # fmt: skip
        self.ledger, self.missing, self.bad, self.quota = ledger, missing, bad, quota
        self.calls: list[str] = []

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if self.quota:
            raise QuotaExceeded("hour")
        self.ledger.check(symbol)
        self.ledger.record(symbol)
        self.calls.append(symbol)
        if symbol in self.missing:
            raise DataUnavailableError(f"{symbol}：Tiingo 没有该代码的日线")
        df = sample(end)
        if symbol in self.bad:
            df.iloc[-5, df.columns.get_loc("close")] = 1e6  # 单日暴涨，校验不通过
        return df


class Env:
    def __init__(self, tmp_path: Path, monthly: int = 500, today: date = TODAY) -> None:
        self.now = datetime(today.year, today.month, today.day, 22, tzinfo=UTC)
        limits = TiingoSettings(hourly=10_000, daily=10_000, monthly_symbols=monthly)
        self.ledger = QuotaLedger(tmp_path / "state" / "q.json", limits, lambda: self.now)
        self.store = bd.backtest_store(tmp_path)
        self.state = bd.BackfillState.load(tmp_path / "state" / "backtest_data.json")
        self.wanted = bd.Wanted(fixed=["QQQ", "SPY"], current=["AAA", "BBB"], delisted=["OLD"])
        self.today = today

    def step(self, provider: DataProvider) -> bd.StepResult:
        return bd.backfill_step(provider, self.store, self.state, self.ledger, self.wanted,
                                self.today, CAL)  # fmt: skip


def test_priority_fixed_then_current_then_delisted(tmp_path: Path) -> None:
    env = Env(tmp_path)
    p = FakeProvider(env.ledger)
    got = [env.step(p).symbol for _ in range(6)]
    assert got == ["QQQ", "SPY", "AAA", "BBB", "OLD", None]
    assert env.step(p).outcome == "idle"
    assert len(env.store.read_bars("SPY")) > 2000


def test_monthly_budget_defers_new_symbols(tmp_path: Path) -> None:
    env = Env(tmp_path, monthly=3)
    p = FakeProvider(env.ledger)
    assert [env.step(p).symbol for _ in range(3)] == ["QQQ", "SPY", "AAA"]
    r = env.step(p)
    assert r.outcome == "idle" and r.symbol is None  # BBB、OLD 等下个月
    env.state.symbols["SPY"].last_fetch = (TODAY - timedelta(days=40)).isoformat()
    assert env.step(p).symbol == "SPY"  # 本月已用过的代码可以刷新


def test_refresh_after_30_days_active_only(tmp_path: Path) -> None:
    env = Env(tmp_path)
    p = FakeProvider(env.ledger)
    for _ in range(5):
        env.step(p)
    old = (TODAY - timedelta(days=31)).isoformat()
    env.state.symbols["AAA"].last_fetch = old
    env.state.symbols["OLD"].last_fetch = old
    assert env.step(p).symbol == "AAA"
    assert env.step(p).outcome == "idle"  # 已退市的不刷新


def test_unavailable_symbol_not_retried_same_month(tmp_path: Path) -> None:
    env = Env(tmp_path)
    p = FakeProvider(env.ledger, missing=frozenset({"SPY"}))
    results = [env.step(p) for _ in range(6)]
    assert results[1].symbol == "SPY" and results[1].outcome == "unavailable"
    assert p.calls.count("SPY") == 1
    assert "没有该代码" in env.state.symbols["SPY"].reason
    env.now = datetime(2026, 11, 2, 22, tzinfo=UTC)  # 下个月再试
    env.today = date(2026, 11, 2)
    assert env.step(p).symbol == "SPY"


def test_stops_when_quota_exhausted(tmp_path: Path) -> None:
    env = Env(tmp_path)
    r = env.step(FakeProvider(env.ledger, quota=True))
    assert r.outcome == "quota" and r.symbol == "QQQ"
    assert "QQQ" not in env.state.symbols
    assert env.step(FakeProvider(env.ledger)).symbol == "QQQ"


def test_invalid_data_not_written(tmp_path: Path) -> None:
    env = Env(tmp_path)
    r = env.step(FakeProvider(env.ledger, bad=frozenset({"QQQ"})))
    assert r.outcome == "invalid"
    assert env.store.read_bars("QQQ").empty
    assert env.state.symbols["QQQ"].status == "invalid"


def test_state_persists(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.step(FakeProvider(env.ledger))
    again = bd.BackfillState.load(tmp_path / "state" / "backtest_data.json")
    assert (
        again.symbols["QQQ"].status == "ok" and again.symbols["QQQ"].last_date == TODAY.isoformat()
    )


def test_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ParquetStore(tmp_path)
    store.write_bars("SPY", sample(date(2026, 10, 1)), "tiingo")
    before = store.read_bars("SPY")

    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.raises(OSError):
        store.write_bars("SPY", sample(), "tiingo")
    monkeypatch.undo()
    pd.testing.assert_frame_equal(store.read_bars("SPY"), before)


def test_status_data_end_and_progress(tmp_path: Path) -> None:
    env = Env(tmp_path)
    p = FakeProvider(env.ledger, missing=frozenset({"OLD"}))
    env.step(p)
    st = bd.status(env.wanted, env.state, env.ledger, None)
    assert (st.fixed_done, st.fixed_total) == (1, 2) and st.data_end is None
    for _ in range(4):
        env.step(p)
    st = bd.status(env.wanted, env.state, env.ledger, None)
    assert (st.fixed_done, st.current_done, st.delisted_done) == (2, 2, 0)
    assert st.data_end == TODAY and st.unavailable == ["OLD"]
    assert st.quota.month_symbols == 5 and st.earnings == "none"


def test_wanted_symbols() -> None:
    uni = Constituents({date(2015, 1, 2): frozenset({"AAA", "OLD"}),
                        date(2020, 1, 2): frozenset({"AAA", "NEW"})})  # fmt: skip
    w = bd.wanted_symbols(uni, TODAY)
    assert {"SPY", "QQQ", "TLT"} <= set(w.fixed)
    assert w.current == ["AAA", "NEW"] and w.delisted == ["OLD"]
    assert bd.wanted_symbols(None, TODAY).current == []


def test_earnings_sync_requires_user_agent(tmp_path: Path) -> None:
    env = Env(tmp_path)
    for _ in range(4):
        env.step(FakeProvider(env.ledger))
    calls: list[str] = []

    def fetch_earnings(symbol: str, ua: str) -> list[date]:
        calls.append(symbol)
        return [date(2026, 7, 30)]

    assert bd.sync_earnings_step(env.state, tmp_path, None, env.wanted, TODAY, fetch_earnings) == 0
    n = bd.sync_earnings_step(env.state, tmp_path, "me test@example.com", env.wanted, TODAY,
                              fetch_earnings)  # fmt: skip
    assert n == 3 and set(calls) == {"AAA", "BBB", "OLD"}
    assert (tmp_path / "events" / "earnings_sp500.csv").exists()
    assert bd.status(env.wanted, env.state, env.ledger, None).earnings == "complete"
    assert bd.sync_earnings_step(env.state, tmp_path, "me test@example.com", env.wanted, TODAY,
                                 fetch_earnings) == 0  # 本月已完成  # fmt: skip


class FakeBackfill:
    def __init__(self) -> None:
        self.steps = 0

    def maybe_step(self) -> None:
        self.steps += 1


def test_worker_backfills_only_when_idle() -> None:
    from quant.jobs.backtest_worker import worker_tick

    bf = FakeBackfill()
    assert worker_tick(lambda: 7, bf) == 7 and bf.steps == 0
    assert worker_tick(lambda: None, bf) is None and bf.steps == 1
    assert worker_tick(lambda: None, None) is None


def test_runner_respects_interval(tmp_path: Path) -> None:
    t = [1000.0]
    env = Env(tmp_path)
    p = FakeProvider(env.ledger)
    r = bd.BackfillRunner(p, env.store, env.state, env.ledger, lambda: env.wanted, CAL,
                          interval=72.0, clock=lambda: t[0], today=lambda: TODAY)  # fmt: skip
    r.maybe_step()
    r.maybe_step()
    assert p.calls == ["QQQ"]
    t[0] += 73
    r.maybe_step()
    assert p.calls == ["QQQ", "SPY"]
