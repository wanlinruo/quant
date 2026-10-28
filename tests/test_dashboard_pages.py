"""交易页、系统页：无数据库与有数据两种情况（后者需要数据库）。"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import streamlit as st
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from quant.db.models import (
    AccountSnapshotRow,
    BacktestRun,
    Fill,
    JobRun,
    Order,
    PositionSnapshot,
    Signal,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")


def _trading() -> None:
    from quant.dashboard.pages import trading

    trading.render()


def _system() -> None:
    from quant.dashboard.pages import system

    system.render()


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()


@pytest.mark.parametrize("page", [_trading, _system])
def test_pages_without_db(no_db: None, page: Any) -> None:
    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception
    assert any("未配置数据库" in w.value for w in at.warning)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.delenv("TIGER_PROPS_PATH", raising=False)
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    st.cache_resource.clear()
    st.cache_data.clear()
    yield DB_URL
    st.cache_resource.clear()
    st.cache_data.clear()


@pytest.mark.db
@needs_db
def test_empty_db_pages(db: str) -> None:
    at = AppTest.from_function(_trading, default_timeout=30).run()
    assert not at.exception
    assert any("还没有交易记录" in i.value for i in at.info)
    at = AppTest.from_function(_system, default_timeout=30).run()
    assert not at.exception
    flag = next(m for m in at.metric if "熔断" in m.label)
    assert flag.value == "关闭（禁止下单）"  # 初始迁移写入 false
    assert not at.button  # 系统页没有任何按钮


@pytest.mark.db
@needs_db
def test_pages_with_data(db: str) -> None:
    now = datetime.now(UTC)
    eng = create_engine(db)
    with Session(eng) as s, s.begin():
        s.add_all(
            [
                AccountSnapshotRow(
                    snapshot_date=date(2026, 10, 27),
                    mode="paper",
                    cash=Decimal("2000"),
                    net_liquidation=Decimal("100000"),
                ),
                AccountSnapshotRow(
                    snapshot_date=date(2026, 10, 28),
                    mode="paper",
                    cash=Decimal("2100"),
                    net_liquidation=Decimal("101000"),
                ),
                PositionSnapshot(
                    snapshot_date=date(2026, 10, 28),
                    mode="paper",
                    symbol="QQQ",
                    qty=180,
                    avg_cost=Decimal("540"),
                    market_value=Decimal("98900"),
                ),
                Signal(
                    strategy="etf_momentum",
                    signal_date=date(2026, 10, 28),
                    target_weights={"QQQ": 1.0},
                    data_as_of=date(2026, 10, 28),
                ),
                Order(
                    client_order_id="2026-10-28-etf_momentum-QQQ-BUY-1",
                    broker_order_id="1",
                    mode="paper",
                    symbol="QQQ",
                    side="BUY",
                    order_type="LMT",
                    qty=180,
                    limit_price=Decimal("541"),
                    reference_price=Decimal("540"),
                    status="FILLED",
                    filled_qty=180,
                    avg_fill_price=Decimal("540.5"),
                ),
                Fill(
                    broker_order_id="1",
                    mode="paper",
                    symbol="QQQ",
                    side="BUY",
                    qty=180,
                    price=Decimal("540.5"),
                    commission=Decimal("1"),
                    filled_at=now,
                ),
                JobRun(
                    job_name="daily_data",
                    trade_date=date(2026, 10, 28),
                    status="success",
                    started_at=now,
                    finished_at=now,
                ),
                JobRun(
                    job_name="daily_signal",
                    trade_date=date(2026, 10, 28),
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    error="数据校验未通过\n详情",
                ),
                BacktestRun(
                    strategy="etf_momentum",
                    params={},
                    start=date(2020, 1, 2),
                    end=date(2021, 1, 4),
                    status="queued",
                ),
            ]
        )
    eng.dispose()

    at = AppTest.from_function(_trading, default_timeout=30).run()
    assert not at.exception
    assert any(m.label == "净值" and "101,000" in m.value for m in at.metric)
    assert len(at.dataframe) == 4  # 目标与实际、持仓、订单、成交

    at = AppTest.from_function(_system, default_timeout=30).run()
    assert not at.exception
    assert any(m.label == "queued" and m.value == "1" for m in at.metric)
    assert not at.button
