"""数据库表结构测试。

前半部分只检查表定义；带 db 标记的测试需要一个可以清空的 PostgreSQL，
地址由 QUANT_TEST_DATABASE_URL 提供（CI 中由 postgres service 提供），未设置时跳过。
"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, UniqueConstraint, create_engine, text
from sqlalchemy.exc import IntegrityError

from quant.core.models import OrderState
from quant.db.models import Base, Order

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLES = {
    "job_runs",
    "signals",
    "orders",
    "fills",
    "position_snapshots",
    "account_snapshots",
    "backtest_runs",
    "backtest_equity",
    "system_flags",
}


def _unique_sets(table: str) -> set[tuple[str, ...]]:
    t = Base.metadata.tables[table]
    out = {
        tuple(c.name for c in uc.columns)
        for uc in t.constraints
        if isinstance(uc, UniqueConstraint)
    }
    out |= {(c.name,) for c in t.columns if c.unique}
    return out


def test_all_tables_defined() -> None:
    assert set(Base.metadata.tables) == TABLES


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("job_runs", ("job_name", "trade_date")),
        ("orders", ("client_order_id",)),
        ("signals", ("strategy", "signal_date")),
        ("account_snapshots", ("snapshot_date", "mode")),
        ("position_snapshots", ("snapshot_date", "mode", "symbol")),
    ],
)
def test_unique_constraints(table: str, columns: tuple[str, ...]) -> None:
    assert columns in _unique_sets(table)


def test_order_statuses_match_domain() -> None:
    assert set(Order.STATUSES) == {s.value for s in OrderState}


# ---- 需要 PostgreSQL ----

DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")


def _alembic_cfg() -> Config:
    return Config(os.path.join(REPO, "alembic.ini"))


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    cfg = _alembic_cfg()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(DB_URL)
    yield eng
    eng.dispose()


@pytest.mark.db
@needs_db
def test_migration_matches_models(engine: Engine) -> None:
    command.check(_alembic_cfg())  # 模型与迁移不一致时抛出异常


@pytest.mark.db
@needs_db
def test_trading_disabled_by_default(engine: Engine) -> None:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT value FROM system_flags WHERE key = 'trading_enabled'")
        ).scalar_one()
    assert value == "false"


def _insert_order(conn: object, cid: str, **overrides: object) -> None:
    row: dict[str, object] = {
        "cid": cid, "mode": "paper", "symbol": "SPY", "side": "BUY", "type": "LMT",
        "qty": 1, "price": Decimal("500.12"), "status": "SUBMITTED",
    }  # fmt: skip
    row.update(overrides)
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO orders (client_order_id, mode, symbol, side, order_type, qty,"
            " limit_price, status) VALUES (:cid, :mode, :symbol, :side, :type, :qty,"
            " :price, :status)"
        ),
        row,
    )


@pytest.mark.db
@needs_db
def test_client_order_id_unique(engine: Engine) -> None:
    cid = "2026-10-29-etf_momentum-SPY-BUY-1"
    with engine.begin() as conn:
        _insert_order(conn, cid)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        _insert_order(conn, cid)


@pytest.mark.db
@needs_db
@pytest.mark.parametrize(
    "overrides", [{"mode": "prod"}, {"status": "NEW"}, {"qty": 0}, {"side": "HOLD"}]
)
def test_order_check_constraints(engine: Engine, overrides: dict[str, object]) -> None:
    with pytest.raises(IntegrityError), engine.begin() as conn:
        _insert_order(conn, "2026-10-29-etf_momentum-SPY-BUY-9", **overrides)


@pytest.mark.db
@needs_db
def test_job_run_unique_per_day(engine: Engine) -> None:
    sql = text(
        "INSERT INTO job_runs (job_name, trade_date, status, started_at)"
        " VALUES ('daily_data', :d, 'running', :t)"
    )
    args = {"d": date(2026, 10, 29), "t": datetime.now(UTC)}
    with engine.begin() as conn:
        conn.execute(sql, args)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(sql, args)


@pytest.mark.db
@needs_db
def test_money_roundtrip_exact(engine: Engine) -> None:
    with engine.begin() as conn:
        _insert_order(conn, "2026-10-29-etf_momentum-SPY-BUY-2", price=Decimal("0.1"))
        got = conn.execute(
            text("SELECT limit_price FROM orders WHERE client_order_id LIKE '%-2'")
        ).scalar_one()
    assert got == Decimal("0.1")


@pytest.mark.db
@needs_db
def test_backtest_run_roundtrip(engine: Engine) -> None:
    import pandas as pd
    from sqlalchemy.orm import Session

    from quant.db.backtests import complete_run, create_run, fail_run
    from quant.db.models import BacktestEquity, BacktestRun

    eq = pd.Series([100.0, 110.0, 99.0], index=pd.bdate_range("2026-10-26", periods=3))
    dd = eq / eq.cummax() - 1
    with Session(engine) as s, s.begin():
        rid = create_run(
            s, "etf_momentum", {"lookback": 126}, date(2026, 10, 26), date(2026, 10, 28)
        )
        complete_run(s, rid, {"cagr": 0.1, "calmar": None}, eq, dd)
        complete_run(s, rid, {"cagr": 0.1, "calmar": None}, eq, dd)  # 重复写入不重复插行
        bad = create_run(s, "etf_momentum", {}, date(2026, 10, 26), date(2026, 10, 28))
        fail_run(s, bad, "boom")
    with Session(engine) as s:
        run = s.get(BacktestRun, rid)
        assert (
            run is not None
            and run.status == "done"
            and run.metrics == {"cagr": 0.1, "calmar": None}
        )
        assert s.query(BacktestEquity).filter_by(run_id=rid).count() == 3
        failed = s.get(BacktestRun, bad)
        assert failed is not None and failed.status == "failed" and failed.error == "boom"
