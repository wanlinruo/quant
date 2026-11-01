"""面板接口测试的公用夹具与示例数据。带 db 标记的测试需要 QUANT_TEST_DATABASE_URL。"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from quant.core.calendar import get_calendar
from quant.data.store import ParquetStore
from quant.db.models import (
    AccountSnapshotRow,
    BacktestRun,
    Fill,
    JobRun,
    Order,
    PositionSnapshot,
    Signal,
)

REPO = Path(__file__).resolve().parents[1]
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")

SEED_FIRST_DAY = date(2026, 10, 27)
SEED_LAST_DAY = date(2026, 10, 28)


@pytest.fixture
def db_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """每个测试一个干净的库（迁移到最新）。"""
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    monkeypatch.delenv("TIGER_PROPS_PATH", raising=False)
    cfg = Config(str(REPO / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield DB_URL


def seed(engine: Engine, mode: str = "paper") -> None:
    """写入一组覆盖各页面的示例数据。"""
    from sqlalchemy.orm import Session

    now = datetime.now(UTC)
    with Session(engine) as s, s.begin():
        s.add_all(
            [
                AccountSnapshotRow(snapshot_date=SEED_FIRST_DAY, mode=mode,
                                   cash=Decimal("2000"), net_liquidation=Decimal("100000")),
                AccountSnapshotRow(snapshot_date=SEED_LAST_DAY, mode=mode,
                                   cash=Decimal("2100"), net_liquidation=Decimal("101000")),
                PositionSnapshot(snapshot_date=SEED_FIRST_DAY, mode=mode, symbol="AAPL", qty=10,
                                 avg_cost=Decimal("200"), market_value=Decimal("2000")),
                PositionSnapshot(snapshot_date=SEED_LAST_DAY, mode=mode, symbol="NVDA", qty=300,
                                 avg_cost=Decimal("130"), market_value=Decimal("60000")),
                PositionSnapshot(snapshot_date=SEED_LAST_DAY, mode=mode, symbol="META", qty=60,
                                 avg_cost=Decimal("600"), market_value=Decimal("38900")),
                Signal(mode=mode, strategy="combo", signal_date=SEED_LAST_DAY,
                       data_as_of=SEED_LAST_DAY, target_weights={"NVDA": 0.6, "META": 0.4}),
                Order(client_order_id="2026-10-28-combo-NVDA-BUY-1", broker_order_id="1",
                      mode=mode, symbol="NVDA", side="BUY", order_type="LMT", qty=300,
                      limit_price=Decimal("131"), reference_price=Decimal("130"),
                      status="FILLED", filled_qty=300, avg_fill_price=Decimal("130.5")),
                Order(client_order_id="2026-10-28-combo-AAPL-SELL-1", broker_order_id="2",
                      mode=mode, symbol="AAPL", side="SELL", order_type="LMT", qty=10,
                      limit_price=Decimal("199"), reference_price=Decimal("200"),
                      status="CANCELLED", filled_qty=0, reason="超时撤单"),
                Fill(broker_order_id="1", mode=mode, symbol="NVDA", side="BUY", qty=300,
                     price=Decimal("130.5"), commission=Decimal("1.17"), filled_at=now),
                JobRun(mode=mode, job_name="daily_data", trade_date=SEED_LAST_DAY,
                       status="success", started_at=now, finished_at=now),
                JobRun(mode=mode, job_name="daily_signal", trade_date=SEED_LAST_DAY,
                       status="failed", started_at=now, finished_at=now,
                       error="数据校验未通过\n详情"),
                BacktestRun(strategy="combo",
                            params={"top_n": 10, "_costs": {"slippage": "0.0005"}},
                            start=date(2020, 1, 2), end=date(2021, 1, 4), status="queued"),
                BacktestRun(strategy="combo", params={}, start=date(2020, 1, 2),
                            end=date(2021, 1, 4), status="failed", error="数据缺失\nTraceback"),
            ]
        )  # fmt: skip


def write_sample_bars(store: ParquetStore, symbol: str) -> None:
    """最近两年的连续交易日样本（校验应通过）。"""
    cal = get_calendar()
    end = cal.last_completed_trading_day(datetime.now(UTC))
    days = pd.DatetimeIndex(cal.trading_days(end.replace(year=end.year - 2), end), name="date")
    close = 100 + np.arange(len(days)) * 0.1
    df = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "adj_close": close,
         "volume": np.full(len(days), 1000, dtype="int64")},
        index=days,
    )  # fmt: skip
    store.write_bars(symbol, df, "test")


def valid_submit_body() -> dict[str, Any]:
    """一份合法的回测提交请求。"""
    return {
        "strategy": "etf_momentum",
        "start": "2022-01-03",
        "end": "2024-01-02",
        "universe": ["SPY", "QQQ", "TLT"],
        "safe_asset": "SHY",
        "lookback": 126,
        "top_k": 1,
        "sma_window": 200,
        "cash": "100000",
        "costs": {"commission_per_share": "0.005", "min_commission": "1", "slippage": "0.0005"},
    }
