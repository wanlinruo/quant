"""面板接口的纯查询层。带 db 标记的测试需要 QUANT_TEST_DATABASE_URL。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api_fixtures import SEED_LAST_DAY, needs_db, seed, write_sample_bars
from quant.api import queries
from quant.api.schemas import QueueStatus
from quant.core.calendar import get_calendar
from quant.data.store import ParquetStore


@pytest.mark.db
@needs_db
def test_positions_latest_snapshot(db_url: str) -> None:
    eng = create_engine(db_url)
    seed(eng, "paper")
    with Session(eng) as s:
        rows = queries.positions(s, "paper")
        assert {r.symbol for r in rows} == {"NVDA", "META"}
        assert all(r.snapshot_date == SEED_LAST_DAY for r in rows)
        assert queries.positions(s, "live") == []
    eng.dispose()


@pytest.mark.db
@needs_db
def test_accounts_reports_has_data(db_url: str) -> None:
    eng = create_engine(db_url)
    seed(eng, "paper")
    with Session(eng) as s:
        assert queries.accounts(s) == {"sim": False, "paper": True, "live": False}
    eng.dispose()


@pytest.mark.db
@needs_db
def test_curve_orders_fills_signals(db_url: str) -> None:
    eng = create_engine(db_url)
    seed(eng, "paper")
    with Session(eng) as s:
        curve = queries.account_curve(s, "paper")
        assert [p.net_liquidation for p in curve] == [100000.0, 101000.0]
        orders = queries.orders(s, "paper")
        assert {o.status for o in orders} == {"FILLED", "CANCELLED"}
        assert next(o for o in orders if o.status == "CANCELLED").reason == "超时撤单"
        [fill] = queries.fills(s, "paper")
        assert fill.price == 130.5 and fill.commission == 1.17
        [sig] = queries.latest_signals(s, "paper")
        assert sig.strategy == "combo" and sig.target_weights == {"NVDA": 0.6, "META": 0.4}
        assert queries.orders(s, "live") == [] and queries.fills(s, "live") == []
    eng.dispose()


@pytest.mark.db
@needs_db
def test_backtest_runs_strip_private_params(db_url: str) -> None:
    eng = create_engine(db_url)
    seed(eng)
    with Session(eng) as s:
        runs = queries.backtest_runs(s)
        assert all(not k.startswith("_") for r in runs for k in r.params)
        queued = next(r for r in runs if r.status == "queued")
        assert queued.costs == {"slippage": "0.0005"} and queued.params == {"top_n": 10}
        failed = next(r for r in runs if r.status == "failed")
        assert failed.error == "数据缺失"
        assert queries.backtest_equity(s, [queued.id]) == []
    eng.dispose()


@pytest.mark.db
@needs_db
def test_queue_and_failures(db_url: str) -> None:
    eng = create_engine(db_url)
    seed(eng)
    with Session(eng) as s:
        assert queries.queue_status(s) == QueueStatus(queued=1, failed=1)
        failures = queries.recent_failures(s)
        sources = [f.source for f in failures]
        assert any(x.startswith("任务 ") for x in sources)
        assert any(x.startswith("回测 #") for x in sources)
        assert all("\n" not in f.message for f in failures)
        jobs = {j.job_name: j for j in queries.job_status(s)}
        assert jobs["daily_signal"].error == "数据校验未通过"
        assert queries.flags(s)["trading_enabled"] == "false"
        assert queries.last_job_finished(s) is not None
    eng.dispose()


def test_data_status_and_bars(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    write_sample_bars(store, "SPY")
    cal = get_calendar()
    expected = cal.last_completed_trading_day(datetime.now(UTC))
    [row] = queries.data_status(store, cal, expected)
    assert row.symbol == "SPY" and row.ok and row.last_date == expected
    assert row.sources == "test" and row.issues == ""
    assert len(queries.bars(store, "SPY", expected - timedelta(days=30))) > 15
    assert queries.bars(store, "NOPE", None) == []
