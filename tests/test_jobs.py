"""每日任务与调度：计划时间（纯函数）+ 需要 PostgreSQL 的幂等与端到端流程。"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from fakes import Clock, FakeBroker, Notes
from quant.core.calendar import get_calendar
from quant.core.settings import ScheduleSettings, Settings
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider, normalize_bars
from quant.data.store import ParquetStore
from quant.db.models import AccountSnapshotRow, JobRun, PositionSnapshot, Signal, SystemFlag
from quant.engine.executor import ET
from quant.jobs import daily
from quant.jobs.daily import JobContext, daily_data, daily_report, daily_signal
from quant.jobs.runner import JobStopped, job_lock, run_job
from quant.jobs.scheduler import Scheduler, day_plan

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")
D = Decimal
DAY = date(2026, 10, 28)  # 周三
NEXT = date(2026, 10, 29)
DRIFT = {"SPY": 0.0004, "QQQ": 0.0009, "TLT": 0.0001, "GLD": 0.0002, "SHY": 0.00008}


def et(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


# ---- 计划时间 ----


def test_day_plan_regular() -> None:
    plan = dict(day_plan(DAY, ScheduleSettings(), get_calendar()))
    assert plan == {
        "daily_execute": et(DAY, 9, 50),
        "daily_report": et(DAY, 9, 50),
        "daily_data": et(DAY, 16, 30),
        "daily_signal": et(DAY, 17, 0),
    }
    names = [n for n, _ in day_plan(DAY, ScheduleSettings(), get_calendar())]
    assert names.index("daily_execute") < names.index("daily_report")


def test_day_plan_half_day_and_holiday() -> None:
    cal = get_calendar()
    half = date(2026, 11, 27)  # 感恩节次日，13:00 收盘
    plan = dict(day_plan(half, ScheduleSettings(), cal))
    assert plan["daily_data"] == et(half, 13, 30)
    assert plan["daily_signal"] == et(half, 14, 0)
    assert plan["daily_execute"] == et(half, 9, 50)
    assert day_plan(date(2026, 11, 26), ScheduleSettings(), cal) == []  # 感恩节休市
    assert day_plan(date(2026, 10, 31), ScheduleSettings(), cal) == []  # 周六


def test_day_plan_execute_not_before_open() -> None:
    plan = dict(day_plan(DAY, ScheduleSettings(daily_execute="09:00"), get_calendar()))
    assert plan["daily_execute"] == et(DAY, 9, 30)


# ---- 需要数据库 ----


class SeriesProvider(DataProvider):
    """固定起点生成的确定性日线；任意区间切片，结果一致。"""

    source = "fake"

    def __init__(self, scale: float = 1.0, last: date = DAY) -> None:
        cal = get_calendar()
        days = pd.DatetimeIndex(cal.trading_days(date(2023, 1, 3), last))
        self.frames = {}
        for sym, drift in DRIFT.items():
            rng = np.random.default_rng(sum(map(ord, sym)))
            px = 100 * np.cumprod(1 + rng.normal(drift, 0.005, len(days))) * scale
            self.frames[sym] = normalize_bars(
                pd.DataFrame(
                    {
                        "open": px,
                        "high": px * 1.005,
                        "low": px * 0.995,
                        "close": px,
                        "adj_close": px,
                        "volume": np.full(len(days), 1000, dtype="int64"),
                    },
                    index=days,
                )
            )

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self.frames[symbol]
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    monkeypatch.setenv("TRADING_MODE", "paper")
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(DB_URL)
    with Session(eng) as s, s.begin():
        s.get(SystemFlag, "trading_enabled").value = "true"  # type: ignore[union-attr]
    yield eng
    eng.dispose()


def make_ctx(engine: Engine, tmp_path: Path, **kw: object) -> JobContext:
    settings = Settings(
        broker="tiger",
        data_dir=tmp_path,
        risk={"symbol_whitelist": list(DRIFT), "max_order_usd": "150000"},
    )
    args: dict[str, object] = {"provider": SeriesProvider(), "notifier": Notes(), **kw}
    clock = args.pop("clock", None)
    ctx = JobContext(settings, engine, store=ParquetStore(tmp_path), **args)  # type: ignore[arg-type]
    if isinstance(clock, Clock):
        ctx.now, ctx.sleep = clock.now, clock.sleep
    else:
        ctx.sleep = lambda s: None
    return ctx


def status(engine: Engine, name: str, d: date) -> str | None:
    with Session(engine) as s:
        return s.scalar(
            select(JobRun.status).where(JobRun.job_name == name, JobRun.trade_date == d)
        )


@pytest.mark.db
@needs_db
class TestRunJob:
    def test_success_then_skipped(self, engine: Engine) -> None:
        calls: list[int] = []

        def job() -> str:
            calls.append(1)
            return "ok"

        n = Notes()
        assert run_job(engine, "daily_data", DAY, job, n) == "success"
        assert run_job(engine, "daily_data", DAY, job, n) == "skipped"
        assert calls == [1] and status(engine, "daily_data", DAY) == "success"

    def test_failure_notifies_and_needs_manual_rerun(self, engine: Engine) -> None:
        n = Notes()

        def boom() -> str:
            raise RuntimeError("数据源超时")

        assert run_job(engine, "daily_data", DAY, boom, n) == "failed"
        assert "数据源超时" in n.sent[-1][1]
        with Session(engine) as s:
            row = s.scalars(select(JobRun)).one()
            assert row.status == "failed" and "RuntimeError" in (row.error or "")
        assert run_job(engine, "daily_data", DAY, lambda: "ok", n) == "skipped"
        assert run_job(engine, "daily_data", DAY, lambda: "ok", n, rerun_failed=True) == "success"

    def test_quiet_errors_not_notified_twice(self, engine: Engine) -> None:
        n = Notes()

        def stopped() -> str:
            raise JobStopped("熔断")

        assert run_job(engine, "daily_execute", DAY, stopped, n) == "failed"
        assert n.sent == []

    def test_stale_running_is_rerun(self, engine: Engine) -> None:
        with Session(engine) as s, s.begin():
            s.add(JobRun(job_name="daily_execute", trade_date=DAY, status="running",
                         started_at=datetime.now(UTC)))  # fmt: skip
        assert run_job(engine, "daily_execute", DAY, lambda: "ok", Notes()) == "success"

    def test_locked(self, engine: Engine) -> None:
        with job_lock(engine, "daily_execute", DAY) as got:
            assert got
            assert run_job(engine, "daily_execute", DAY, lambda: "ok", Notes()) == "locked"
        assert status(engine, "daily_execute", DAY) is None


@pytest.mark.db
@needs_db
class TestDailyJobs:
    def test_data_then_signal(self, engine: Engine, tmp_path: Path) -> None:
        ctx = make_ctx(engine, tmp_path)
        run_job(engine, "daily_data", DAY, lambda: daily_data(ctx, DAY), ctx.notifier)
        assert status(engine, "daily_data", DAY) == "success"
        assert ParquetStore(tmp_path).read_bars("QQQ").index[-1].date() == DAY
        text = daily_signal(ctx, DAY)
        with Session(engine) as s:
            sig = s.scalars(select(Signal)).one()
        assert sig.signal_date == DAY and sig.data_as_of == DAY
        assert sig.target_weights == {"QQQ": 1.0}  # 合成数据中 QQQ 动量最强
        assert "QQQ 100%" in text
        title, body, _ = ctx.notifier.sent[-1]  # type: ignore[attr-defined]
        assert "信号" in title and f"{NEXT} 09:50 ET" in body

    def test_signal_requires_data_job(self, engine: Engine, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="daily_data 未成功"):
            daily_signal(make_ctx(engine, tmp_path), DAY)

    def test_data_waits_for_today(self, engine: Engine, tmp_path: Path) -> None:
        # 第一次拉取还没有当日数据，等待后重试成功
        early, full = SeriesProvider(last=date(2026, 10, 27)), SeriesProvider()

        class Late(DataProvider):
            source = "fake"
            calls = 0

            def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
                p = early if Late.calls < len(DRIFT) else full
                Late.calls += 1
                return p.daily_bars(symbol, start, end)

        slept: list[float] = []
        ctx = make_ctx(engine, tmp_path, provider=Late())
        ctx.sleep = slept.append
        daily_data(ctx, DAY)
        assert slept == [daily.DATA_RETRY_SECONDS]

    def test_data_source_mismatch_fails(self, engine: Engine, tmp_path: Path) -> None:
        ctx = make_ctx(engine, tmp_path, secondary=SeriesProvider(scale=1.01))
        with pytest.raises(DataValidationError, match="source_diff"):
            daily_data(ctx, DAY)

    def test_report_snapshot_when_consistent(self, engine: Engine, tmp_path: Path) -> None:
        broker = FakeBroker(Clock(), "1000", {})
        ctx = make_ctx(engine, tmp_path, broker_factory=lambda: broker)
        assert daily_report(ctx, DAY).startswith("净值")
        with Session(engine) as s:
            assert s.scalars(select(AccountSnapshotRow)).one().snapshot_date == DAY

    def test_report_mismatch_halts(self, engine: Engine, tmp_path: Path) -> None:
        broker = FakeBroker(Clock(), "1000", {"SPY": 5})
        ctx = make_ctx(engine, tmp_path, broker_factory=lambda: broker)
        with pytest.raises(JobStopped):
            daily_report(ctx, DAY)
        _, body, _ = ctx.notifier.sent[-1]  # type: ignore[attr-defined]
        assert "对账不一致" in body and "关（不下单）" in body
        with Session(engine) as s:
            assert s.scalars(select(PositionSnapshot)).all() == []

    def test_sim_mode_skips_broker_jobs(self, engine: Engine, tmp_path: Path) -> None:
        ctx = make_ctx(engine, tmp_path)
        assert "跳过" in daily.daily_execute(ctx, DAY)
        assert "跳过" in daily_report(ctx, DAY)


@pytest.mark.db
@needs_db
def test_end_to_end_two_days(engine: Engine, tmp_path: Path) -> None:
    """D 日收盘后：数据 → 信号；D+1 09:50：执行 → 日报。重复触发不会重复下单。"""
    clock = Clock(et(DAY, 16, 31).astimezone(UTC))
    broker = FakeBroker(clock, "100000", {})
    broker.prices = {s: D("100") for s in DRIFT}
    ctx = make_ctx(engine, tmp_path, clock=clock, broker_factory=lambda: broker)
    sched = Scheduler(ctx)

    assert sched.tick() == [("daily_data", "success")]  # 17:00 前不生成信号
    clock.t = et(DAY, 17, 1).astimezone(UTC)
    assert sched.tick() == [("daily_signal", "success")]
    assert sched.tick() == []

    clock.t = et(NEXT, 9, 51).astimezone(UTC)
    broker.quote_time = clock.t
    assert sched.tick() == [("daily_execute", "success"), ("daily_report", "success")]
    assert [(o.symbol, o.side.value) for o in broker.placed] == [("QQQ", "BUY")]
    assert broker.pos["QQQ"] > 0

    # 进程重启（新 Scheduler）后同一天再次触发：job_runs 已成功，不会重复下单
    assert Scheduler(ctx).tick() == [("daily_execute", "skipped"), ("daily_report", "skipped")]
    assert len(broker.placed) == 1
    with Session(engine) as s:
        snap = s.scalars(select(PositionSnapshot)).one()
        assert (snap.snapshot_date, snap.symbol, snap.qty) == (NEXT, "QQQ", broker.pos["QQQ"])


@pytest.mark.db
@needs_db
def test_missed_execution_window(engine: Engine, tmp_path: Path) -> None:
    clock = Clock(et(NEXT, 15, 45).astimezone(UTC))  # 已过 15:30 截止
    broker = FakeBroker(clock, "100000", {})
    ctx = make_ctx(engine, tmp_path, clock=clock, broker_factory=lambda: broker)
    with Session(engine) as s, s.begin():
        s.add(Signal(strategy="etf_momentum", signal_date=DAY, target_weights={"QQQ": 1.0},
                     data_as_of=DAY))  # fmt: skip
    assert Scheduler(ctx).tick() == []
    assert broker.placed == [] and status(engine, "daily_execute", NEXT) is None
    title, _, _ = ctx.notifier.sent[-1]  # type: ignore[attr-defined]
    assert "错过执行" in title


# ---- 组合策略接入（#47 第二份计划任务 5） ----

MONTH_END = date(2026, 10, 30)  # 10 月最后一个交易日
COMBO_PARAMS = {
    "swing": {"stocks": ["TLT", "GLD"], "trend_ma": 20, "support_ma": 5, "exit_ma": 3,
              "replay_days": 60},
}  # fmt: skip
UNIVERSE_CSV = 'date,tickers\n2026-08-01,"AAA,BBB,CCC"\n'


class TigerLike(SeriesProvider):
    """主数据源（老虎）：日线 + 拆股记录 + 财报日历。"""

    def __init__(self, last: date = DAY) -> None:
        super().__init__(last=last)
        self.earnings_calls = 0

    def splits(
        self, symbols: object, start: date, end: date
    ) -> dict[str, list[tuple[date, float]]]:
        return {}

    def earnings_calendar(
        self, symbols: object, start: date, end: date, cal: object
    ) -> dict[str, list[date]]:
        self.earnings_calls += 1
        return {"TLT": [date(2026, 11, 20)]}


class UniverseFake(DataProvider):
    """候选池（新浪）：AAA 稳步上涨且放量，BBB 平，CCC 下跌。"""

    source = "sina"

    def __init__(self, last: date = DAY, fail: tuple[str, ...] = ()) -> None:
        self.fail = set(fail)
        self.calls: list[tuple[str, date]] = []
        days = pd.DatetimeIndex(get_calendar().trading_days(date(2023, 1, 3), last))
        t = np.arange(len(days))
        drift = {"AAA": 0.003, "BBB": 0.0, "CCC": -0.001}
        self.frames = {
            s: normalize_bars(pd.DataFrame(
                {"open": 50 * (1 + g) ** t, "high": 50 * (1 + g) ** t * 1.01,
                 "low": 50 * (1 + g) ** t * 0.99, "close": 50 * (1 + g) ** t,
                 "volume": np.linspace(1e6, 3e6, len(days)).astype("int64")},
                index=days,
            ))
            for s, g in drift.items()
        }  # fmt: skip

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from quant.data.providers.base import DataUnavailableError

        self.calls.append((symbol, end))
        if symbol in self.fail:
            raise DataUnavailableError(symbol)
        df = self.frames[symbol]
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))].assign(
            adj_close=float("nan")
        )


def combo_ctx(engine: Engine, tmp_path: Path, last: date = DAY, **kw: object) -> JobContext:
    from quant.data.universe import constituents_path

    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNIVERSE_CSV, encoding="utf-8")
    settings = Settings(
        broker="tiger",
        data_dir=tmp_path,
        data={"primary": "tiger", "universe": "sina"},
        strategy={"name": "combo", "params": COMBO_PARAMS},
        risk={"symbol_whitelist": list(DRIFT), "max_order_usd": "150000"},
    )
    args: dict[str, object] = {
        "provider": TigerLike(last=last),
        "universe_provider": UniverseFake(last=last),
        "notifier": Notes(),
        **kw,
    }
    ctx = JobContext(settings, engine, store=ParquetStore(tmp_path), **args)  # type: ignore[arg-type]
    ctx.sleep = lambda s: None
    return ctx


def test_universe_requires_tiger_primary() -> None:
    with pytest.raises(ValueError, match=r"data\.universe=sina 需要 data\.primary=tiger"):
        Settings(broker="tiger", data={"primary": "yfinance", "universe": "sina"})
    assert (
        Settings(broker="tiger", data={"primary": "tiger", "universe": "sina"}).data.universe
        == "sina"
    )


def test_store_field_loader_prefers_first_store(tmp_path: Path) -> None:
    from quant.data.universe_bars import universe_store
    from quant.engine.backtester import store_field_loader

    main, uni = ParquetStore(tmp_path), universe_store(tmp_path)
    p = SeriesProvider()
    main.write_bars("SPY", p.daily_bars("SPY", date(2026, 10, 1), DAY), "fake")
    uni.write_bars(
        "SPY", SeriesProvider(scale=2.0).daily_bars("SPY", date(2026, 10, 1), DAY), "sina"
    )
    uni.write_bars("QQQ", p.daily_bars("QQQ", date(2026, 10, 1), DAY), "sina")
    load = store_field_loader([main, uni])
    close = load(["SPY", "QQQ", "ZZZ"], "close", date(2026, 10, 1), DAY)
    assert list(close.columns) == ["SPY", "QQQ", "ZZZ"]
    assert close["SPY"].iloc[-1] == pytest.approx(p.frames["SPY"]["adj_close"].iloc[-1])
    assert close["QQQ"].notna().all() and close["ZZZ"].isna().all()


@pytest.mark.db
@needs_db
class TestComboJobs:
    def test_etf_momentum_unchanged(self, engine: Engine, tmp_path: Path) -> None:
        uni = UniverseFake()
        ctx = make_ctx(engine, tmp_path, universe_provider=uni)
        daily_data(ctx, DAY)
        assert uni.calls == []
        assert not (tmp_path / "universe").exists()

    def test_mid_month_syncs_to_previous_month_end(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path)
        text = daily_data(ctx, DAY)
        uni = ctx.universe_provider
        assert {end for _, end in uni.calls} == {date(2026, 9, 30)}  # type: ignore[union-attr]
        assert "覆盖率" in text
        assert ctx.provider.earnings_calls == 1  # type: ignore[attr-defined]

    def test_month_end_syncs_to_today(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path, last=MONTH_END)
        daily_data(ctx, MONTH_END)
        assert {end for _, end in ctx.universe_provider.calls} == {MONTH_END}  # type: ignore[union-attr]

    def test_low_coverage_fails(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path, universe_provider=UniverseFake(fail=("BBB",)))
        with pytest.raises(DataValidationError, match="覆盖率"):
            daily_data(ctx, DAY)

    def test_combo_signal_includes_momentum_stock(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path)
        run_job(engine, "daily_data", DAY, lambda: daily_data(ctx, DAY), ctx.notifier)
        daily_signal(ctx, DAY)
        with Session(engine) as s:
            sig = s.scalars(select(Signal)).one()
        assert sig.strategy == "combo"
        assert sig.target_weights["AAA"] == pytest.approx(
            0.4 * 0.25
        )  # 动量仓第一名，来自候选池目录
        assert "BBB" not in sig.target_weights and "CCC" not in sig.target_weights


class FailingEarnings(TigerLike):
    def earnings_calendar(
        self, symbols: object, start: date, end: date, cal: object
    ) -> dict[str, list[date]]:
        from quant.data.providers.base import DataUnavailableError

        raise DataUnavailableError("老虎财报日历超时")


class NoEarnings(TigerLike):
    def earnings_calendar(
        self, symbols: object, start: date, end: date, cal: object
    ) -> dict[str, list[date]]:
        return {}


@pytest.mark.db
@needs_db
class TestComboReviewFixes:
    def test_ranking_inputs_frozen_within_month(self, engine: Engine, tmp_path: Path) -> None:
        from quant.data.universe import constituents_path
        from quant.jobs.daily import build_context

        ctx = combo_ctx(engine, tmp_path, last=NEXT)
        daily_data(ctx, DAY)
        # 数据集事后补录了 09-15 生效的 DDD（早于调仓日 09-30）
        constituents_path(tmp_path).write_text(
            UNIVERSE_CSV + '2026-09-15,"AAA,BBB,CCC,DDD"\n', encoding="utf-8"
        )
        uni = UniverseFake(last=NEXT)
        ctx.universe_provider = uni
        daily_data(ctx, NEXT)
        assert uni.calls == []  # 月内不再拉取候选池
        u = build_context(ctx, NEXT).universe
        assert u is not None and u.members(date(2026, 9, 30)) == {"AAA", "BBB", "CCC"}

    def test_earnings_failure_uses_local_file(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path, last=NEXT)
        daily_data(ctx, DAY)  # 写入本地财报日
        ctx.provider = FailingEarnings(last=NEXT)
        daily_data(ctx, NEXT)  # 不失败
        assert any("财报日历" in body for _, body, _ in ctx.notifier.sent)  # type: ignore[attr-defined]

    def test_earnings_failure_without_local_file_fails(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        from quant.data.providers.base import DataUnavailableError

        ctx = combo_ctx(engine, tmp_path, provider=FailingEarnings())
        with pytest.raises(DataUnavailableError):
            daily_data(ctx, DAY)

    def test_missing_earnings_dates_warned(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path, provider=NoEarnings())
        daily_data(ctx, DAY)
        bodies = [body for _, body, _ in ctx.notifier.sent]  # type: ignore[attr-defined]
        assert any("TLT" in b and "GLD" in b and "财报日" in b for b in bodies)


class LateUniverse(UniverseFake):
    """第一轮只有前一交易日的数据（当日未出），之后完整。"""

    def __init__(self, last: date) -> None:
        super().__init__(last=last)
        self.rounds = 0

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if symbol == "AAA":
            self.rounds += 1
        if self.rounds <= 1:
            end = get_calendar().previous_trading_day(end)
        return super().daily_bars(symbol, start, end)


@pytest.mark.db
@needs_db
class TestUniverseNotReady:
    def test_waits_for_today_on_rebalance_day(self, engine: Engine, tmp_path: Path) -> None:
        ctx = combo_ctx(engine, tmp_path, last=MONTH_END, universe_provider=LateUniverse(MONTH_END))
        slept: list[float] = []
        ctx.sleep = slept.append
        text = daily_data(ctx, MONTH_END)
        assert daily.DATA_RETRY_SECONDS in slept
        assert "覆盖率 100.0%" in text
