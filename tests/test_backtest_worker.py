"""backtest-worker 与任务队列。带 db 标记的测试需要 QUANT_TEST_DATABASE_URL。"""

import os
import threading
import time
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from quant.core.settings import CostSettings, Settings
from quant.db.backtests import claim_next, create_run
from quant.db.models import BacktestEquity, BacktestRun
from quant.engine.backtester import BacktestResult
from quant.engine.metrics import compute_metrics, drawdown
from quant.engine.runner import BacktestSpec
from quant.jobs.backtest_worker import process_one

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")
SETTINGS = Settings(broker="sim", TRADING_MODE="sim")


def test_spec_from_stored_uses_recorded_costs() -> None:
    stored = {
        "lookback": 60,
        "_costs": {"commission_per_share": "0.005", "min_commission": "1", "slippage": "0.001"},
        "_cash": "5000",
    }
    spec = BacktestSpec.from_stored(
        "etf_momentum", date(2020, 1, 2), date(2021, 1, 4), stored, SETTINGS
    )
    assert spec.params == {"lookback": 60}
    assert spec.costs.slippage == Decimal("0.001")
    assert spec.cash == Decimal("5000")
    assert (
        BacktestSpec.from_stored(
            "etf_momentum", spec.start, spec.end, spec.stored_params(), SETTINGS
        )
        == spec
    )


def test_spec_without_costs_requires_configured_commission() -> None:
    with pytest.raises(ValueError, match="#5"):
        BacktestSpec.from_stored("etf_momentum", date(2020, 1, 2), date(2021, 1, 4), {}, SETTINGS)
    configured = Settings(
        broker="sim",
        TRADING_MODE="sim",
        costs=CostSettings(commission_per_share=Decimal("0.01"), min_commission=Decimal("2")),
    )
    spec = BacktestSpec.from_stored(
        "etf_momentum", date(2020, 1, 2), date(2021, 1, 4), {}, configured
    )
    assert spec.costs.min_commission == Decimal("2")


# ---- 需要 PostgreSQL ----


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(DB_URL)
    yield eng
    eng.dispose()


def _enqueue(engine: Engine, n: int, params: dict[str, object] | None = None) -> list[int]:
    zero = {"commission_per_share": "0", "min_commission": "0", "slippage": "0"}
    stored = params if params is not None else {"_costs": zero, "_cash": "100000"}
    with Session(engine) as s, s.begin():
        return [
            create_run(s, "etf_momentum", stored, date(2020, 1, 2), date(2021, 1, 4))
            for _ in range(n)
        ]


def _fake_result() -> BacktestResult:
    eq = pd.Series([100000.0, 101000.0, 99000.0], index=pd.bdate_range("2020-01-02", periods=3))
    return BacktestResult(eq, drawdown(eq), pd.DataFrame(), [], compute_metrics(eq), None)


@pytest.mark.db
@needs_db
def test_concurrent_claims_never_duplicate(engine: Engine) -> None:
    ids = _enqueue(engine, 12)
    claimed: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        while True:
            with Session(engine) as s, s.begin():
                run = claim_next(s)
                if run is None:
                    return
                time.sleep(0.05)  # 持有行锁一段时间，制造并发重叠
                with lock:
                    claimed.append(run.id)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids)  # 每个任务恰好被领取一次
    with Session(engine) as s:
        statuses = set(s.scalars(select(BacktestRun.status)))
    assert statuses == {"running"}


@pytest.mark.db
@needs_db
def test_process_one_success_and_order(engine: Engine) -> None:
    first, second = _enqueue(engine, 2)
    seen: list[BacktestSpec] = []

    def runner(spec: BacktestSpec, settings: Settings) -> BacktestResult:
        seen.append(spec)
        return _fake_result()

    assert process_one(engine, SETTINGS, runner) == first  # 先进先出
    with Session(engine) as s:
        run = s.get(BacktestRun, first)
        assert run is not None and run.status == "done"
        assert run.started_at is not None and run.finished_at is not None
        assert run.metrics is not None and "cagr" in run.metrics
        assert s.query(BacktestEquity).filter_by(run_id=first).count() == 3
        pending = s.get(BacktestRun, second)
        assert pending is not None and pending.status == "queued"
    assert seen[0].strategy == "etf_momentum"


@pytest.mark.db
@needs_db
def test_process_one_failure_recorded(engine: Engine) -> None:
    (rid,) = _enqueue(engine, 1)

    def runner(spec: BacktestSpec, settings: Settings) -> BacktestResult:
        raise RuntimeError("数据源不可用")

    assert process_one(engine, SETTINGS, runner) == rid
    with Session(engine) as s:
        run = s.get(BacktestRun, rid)
        assert run is not None and run.status == "failed"
        assert run.error is not None and "数据源不可用" in run.error
    assert process_one(engine, SETTINGS, runner) is None  # 队列已空


@pytest.mark.db
@needs_db
def test_bad_params_fail_instead_of_crash(engine: Engine) -> None:
    (rid,) = _enqueue(engine, 1, params={"lookbak": 1})  # 未记录成本、参数拼错
    assert process_one(engine, SETTINGS) == rid
    with Session(engine) as s:
        run = s.get(BacktestRun, rid)
        assert run is not None and run.status == "failed"
