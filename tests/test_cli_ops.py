"""运维命令 baseline / trading：需要 PostgreSQL（QUANT_TEST_DATABASE_URL），否则跳过。"""

import argparse
import io
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from fakes import Clock, FakeBroker
from quant import cli
from quant.broker import tiger
from quant.core.settings import Settings
from quant.db.models import AccountSnapshotRow, PositionSnapshot, SystemFlag
from quant.engine.reconcile import expected_positions, trading_enabled

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL"),
]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    monkeypatch.setenv("TRADING_MODE", "paper")
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(DB_URL)
    yield eng
    eng.dispose()


class FakeTiger(FakeBroker):
    masked_account = "***0000"


def run_baseline(monkeypatch: pytest.MonkeyPatch, now: datetime, confirm: bool = False,
                 positions: dict[str, int] | None = None) -> tuple[int, str]:  # fmt: skip
    broker = FakeTiger(Clock(now), "1000", positions or {"QQQ": 10})
    broker.prices["QQQ"] = Decimal("500")
    monkeypatch.setattr(tiger.TigerBroker, "from_settings", classmethod(lambda c, s: broker))

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return now

    monkeypatch.setattr(cli, "datetime", FixedNow)
    out = io.StringIO()
    rc = cli.baseline(argparse.Namespace(confirm=confirm), Settings(broker="tiger"), out)
    return rc, out.getvalue()


def test_baseline_dated_last_close(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    rc, out = run_baseline(monkeypatch, datetime(2026, 10, 29, 6, 0, tzinfo=UTC))  # 02:00 ET
    assert rc == 0 and "2026-10-28" in out and "***0000" in out
    with Session(engine) as s:
        snap = s.scalars(select(PositionSnapshot)).one()
        assert (snap.snapshot_date, snap.symbol, snap.qty) == (date(2026, 10, 28), "QQQ", 10)
        assert expected_positions(s, "paper") == {"QQQ": 10}


def test_baseline_refused_during_session(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    rc, out = run_baseline(monkeypatch, datetime(2026, 10, 29, 15, 0, tzinfo=UTC))  # 11:00 ET
    assert rc == 1 and "交易时段" in out


def test_baseline_confirm_replaces_later(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    with Session(engine) as s, s.begin():  # 错误地记在了 10-29 的旧基准
        s.add(AccountSnapshotRow(snapshot_date=date(2026, 10, 29), mode="paper",
                                 cash=Decimal(1), net_liquidation=Decimal(1)))  # fmt: skip
        s.add(PositionSnapshot(snapshot_date=date(2026, 10, 29), mode="paper", symbol="SPY",
                               qty=1, avg_cost=Decimal(1), market_value=Decimal(1)))  # fmt: skip
    now = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
    assert run_baseline(monkeypatch, now)[0] == 1  # 已有快照，需要 --confirm
    assert run_baseline(monkeypatch, now, confirm=True)[0] == 0
    with Session(engine) as s:
        dates = {r.snapshot_date for r in s.scalars(select(AccountSnapshotRow))}
        assert dates == {date(2026, 10, 28)}
        assert expected_positions(s, "paper") == {"QQQ": 10}


def test_trading_enable_needs_yes(engine: Engine) -> None:
    settings = Settings(broker="tiger")
    out = io.StringIO()
    args = argparse.Namespace(action="enable", yes=False, reason="")
    assert cli.trading(args, settings, out) == 1
    with Session(engine) as s:
        assert not trading_enabled(s)
    args = argparse.Namespace(action="enable", yes=True, reason="")
    assert cli.trading(args, settings, out) == 0
    args = argparse.Namespace(action="disable", yes=False, reason="测试")
    assert cli.trading(args, settings, out) == 0
    with Session(engine) as s:
        assert not trading_enabled(s)
        reason = s.get(SystemFlag, "halt_reason")
        assert reason is not None and "测试" in reason.value
