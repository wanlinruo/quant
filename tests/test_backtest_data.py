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
from quant.data.providers.tiingo import SymbolNotFound
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
            raise SymbolNotFound(f"{symbol}：Tiingo 没有该代码的日线")
        df = sample(end)
        if symbol in self.bad:
            df.loc[df.index[-5], "close"] = 1e6  # 单日暴涨，校验不通过
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


def test_cli_status_prints_progress(tmp_path: Path) -> None:
    import io

    from quant.cli import backtest_data_command, build_parser
    from quant.core.settings import Settings

    env = Env(tmp_path)
    env.step(FakeProvider(env.ledger))
    env.ledger.path.rename(bd.quota_path(tmp_path))
    settings = Settings(broker="sim", TRADING_MODE="sim", data_dir=tmp_path)
    out = io.StringIO()
    args = build_parser().parse_args(["backtest-data", "status"])
    assert backtest_data_command(args, settings, out) == 0
    text = out.getvalue()
    assert "固定标的 1/" in text and "本月已用" in text and "/500 个标的" in text
    assert "数据截止：尚未补齐" in text


def test_cli_sync_requires_tiingo(tmp_path: Path) -> None:
    import io

    from quant.cli import backtest_data_command, build_parser
    from quant.core.settings import Settings

    settings = Settings(broker="sim", TRADING_MODE="sim", data_dir=tmp_path)
    out = io.StringIO()
    args = build_parser().parse_args(["backtest-data", "sync", "--max", "3"])
    assert backtest_data_command(args, settings, out) == 1
    assert "QUANT_TIINGO_API_KEY" in out.getvalue()


# ---- 最终审查修复（#66）----


class ErrProvider(FakeProvider):
    def __init__(self, ledger: QuotaLedger, exc: Exception) -> None:
        super().__init__(ledger)
        self.exc = exc

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise self.exc


def test_transient_error_does_not_mark_unavailable(tmp_path: Path) -> None:
    env = Env(tmp_path)
    r = env.step(ErrProvider(env.ledger, DataUnavailableError("SPY：Tiingo 返回 502")))
    assert r.outcome == "error" and "QQQ" not in env.state.symbols
    assert env.step(FakeProvider(env.ledger)).symbol == "QQQ"  # 立刻可以重试


def test_auth_error_stops(tmp_path: Path) -> None:
    from quant.data.providers.tiingo import TiingoAuthError

    env = Env(tmp_path)
    r = env.step(ErrProvider(env.ledger, TiingoAuthError("Tiingo key 无效")))
    assert r.outcome == "auth" and env.state.symbols == {}


def test_symbol_not_found_marks_unavailable(tmp_path: Path) -> None:
    from quant.data.providers.tiingo import SymbolNotFound

    env = Env(tmp_path)
    r = env.step(ErrProvider(env.ledger, SymbolNotFound("QQQ：没有")))
    assert r.outcome == "unavailable" and env.state.symbols["QQQ"].status == "unavailable"


def test_new_symbols_before_refresh_in_new_month(tmp_path: Path) -> None:
    """第二个月：先刷新固定标的、再补上月没拿到的当前成分与已退市成分，最后才刷新当前成分。"""
    env = Env(tmp_path, monthly=4)
    env.wanted = bd.Wanted(fixed=["F1"], current=["C1", "C2", "C3"], delisted=["D1", "D2"])
    p = FakeProvider(env.ledger)
    assert [env.step(p).symbol for _ in range(5)] == ["F1", "C1", "C2", "C3", None]  # 本月额度用完
    env.now = datetime(2026, 12, 1, 22, tzinfo=UTC)
    env.today = date(2026, 12, 1)  # 刷新全部到期
    got = [env.step(p).symbol for _ in range(5)]
    assert got == ["F1", "D1", "D2", "C1", None]


def test_lenient_validation_accepts_gap_and_jump(tmp_path: Path) -> None:
    env = Env(tmp_path)

    class GapJump(FakeProvider):
        def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            df: pd.DataFrame = (
                super().daily_bars(symbol, start, end).drop(index=sample().index[100])
            )
            prices = ["open", "high", "low", "close", "adj_close"]
            df.loc[df.index[-3] :, prices] *= 0.2  # 退市前暴跌
            return df

    assert env.step(GapJump(env.ledger)).outcome == "fetched"


def test_bad_ohlc_still_invalid(tmp_path: Path) -> None:
    env = Env(tmp_path)

    class BadOhlc(FakeProvider):
        def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            df = super().daily_bars(symbol, start, end)
            df.loc[df.index[-5], "high"] = 0.5  # 最高价低于收盘价
            return df

    assert env.step(BadOhlc(env.ledger)).outcome == "invalid"


def test_earnings_precondition_ignores_unavailable(tmp_path: Path) -> None:
    from quant.data.providers.tiingo import SymbolNotFound

    env = Env(tmp_path)
    p = FakeProvider(env.ledger)
    env.step(p)
    env.step(p)
    env.step(p)
    env.step(ErrProvider(env.ledger, SymbolNotFound("BBB：没有")))
    n = bd.sync_earnings_step(env.state, tmp_path, "me test@example.com", env.wanted, TODAY,
                              lambda s, ua: [date(2026, 7, 30)])  # fmt: skip
    assert n == 3


def test_default_earnings_fetch_uses_cik_map(monkeypatch: pytest.MonkeyPatch) -> None:
    from quant.data.providers import edgar

    seen: dict[str, object] = {}
    monkeypatch.setattr(edgar, "fetch_cik_map", lambda ua: {"XOM": 34088})

    def fake(symbol: str, ua: str, **kw: object) -> list[date]:
        seen.update(kw, symbol=symbol)
        return [date(2026, 7, 31)]

    monkeypatch.setattr(edgar, "fetch_earnings", fake)
    fetch = bd.edgar_fetcher()
    assert fetch("XOM", "me x@example.com") == [date(2026, 7, 31)]
    assert seen["ciks"] == (34088,)
    assert fetch("NOCIK", "me x@example.com") == []


def test_runner_reloads_state(tmp_path: Path) -> None:
    """命令行刚补过的标的，worker 不会因为内存里的旧状态再拉一次。"""
    env = Env(tmp_path)
    p = FakeProvider(env.ledger)
    r = bd.BackfillRunner(p, env.store, env.state, env.ledger, lambda: env.wanted, CAL,
                          interval=0, clock=lambda: 0.0, today=lambda: TODAY,
                          state_file=bd.state_path(tmp_path))  # fmt: skip
    other = bd.BackfillState.load(bd.state_path(tmp_path))
    other.symbols["QQQ"] = bd.SymbolState("ok", TODAY.isoformat(), "2026-10", TODAY.isoformat())
    other.path = bd.state_path(tmp_path)
    other.save()
    r.maybe_step()
    assert p.calls == ["SPY"]


def test_cli_sync_retry_unavailable_clears(tmp_path: Path) -> None:
    state = bd.BackfillState.load(bd.state_path(tmp_path))
    state.symbols["SPY"] = bd.SymbolState("unavailable", TODAY.isoformat(), "2026-10", reason="x")
    state.symbols["QQQ"] = bd.SymbolState("ok", TODAY.isoformat(), "2026-10", TODAY.isoformat())
    state.save()
    assert bd.clear_unavailable(bd.state_path(tmp_path)) == ["SPY"]
    again = bd.BackfillState.load(bd.state_path(tmp_path))
    assert "SPY" not in again.symbols and "QQQ" in again.symbols
