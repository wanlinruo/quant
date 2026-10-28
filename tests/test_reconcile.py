"""对账：需要 PostgreSQL（QUANT_TEST_DATABASE_URL），否则跳过（compare 除外）。"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from quant.broker.sim import CostModel, SimBroker
from quant.core.models import AccountSnapshot, Position
from quant.db.models import Fill, PositionSnapshot, SystemFlag
from quant.engine.reconcile import (
    compare,
    expected_positions,
    reconcile,
    record_snapshot,
    trading_enabled,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")
D = Decimal


def test_compare() -> None:
    assert compare({"SPY": 10}, {"SPY": 10}) == []
    diffs = compare({"SPY": 10, "TLT": 5}, {"SPY": 9, "GLD": 1})
    assert [(m.symbol, m.expected, m.actual) for m in diffs] == [
        ("GLD", 0, 1), ("SPY", 10, 9), ("TLT", 5, 0),
    ]  # fmt: skip


class FakeBroker(SimBroker):
    def __init__(self, positions: dict[str, int]) -> None:
        super().__init__(D("0"), CostModel(D("0"), D("0")), date(2026, 10, 28))
        self._fake = positions

    def get_positions(self) -> dict[str, Position]:
        return {s: Position(s, q, D("1"), D(q)) for s, q in self._fake.items()}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(DB_URL)
    with Session(eng) as s, s.begin():  # 模拟人工打开开关
        s.get(SystemFlag, "trading_enabled").value = "true"  # type: ignore[union-attr]
    yield eng
    eng.dispose()


def _fill(sym: str, side: str, qty: int, when: datetime, mode: str = "paper") -> Fill:
    return Fill(broker_order_id="x", mode=mode, symbol=sym, side=side, qty=qty,
                price=D("100"), commission=D("1"), filled_at=when)  # fmt: skip


@pytest.mark.db
@needs_db
def test_expected_from_snapshot_plus_later_fills(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        s.add(PositionSnapshot(snapshot_date=date(2026, 10, 27), mode="paper", symbol="SPY",
                               qty=10, avg_cost=D("1"), market_value=D("1")))  # fmt: skip
        # 10-27 当天的成交已包含在快照中（美东 10-27 15:00 = UTC 19:00）
        s.add(_fill("SPY", "BUY", 10, datetime(2026, 10, 27, 19, tzinfo=UTC)))
        # 快照之后：卖 SPY 4，买 TLT 7；另一个账户模式的成交不计入
        s.add(_fill("SPY", "SELL", 4, datetime(2026, 10, 28, 14, tzinfo=UTC)))
        s.add(_fill("TLT", "BUY", 7, datetime(2026, 10, 28, 14, tzinfo=UTC)))
        s.add(_fill("QQQ", "BUY", 3, datetime(2026, 10, 28, 14, tzinfo=UTC), mode="live"))
    with Session(engine) as s:
        assert expected_positions(s, "paper") == {"SPY": 6, "TLT": 7}
        assert expected_positions(s, "live") == {"QQQ": 3}


@pytest.mark.db
@needs_db
def test_consistent_keeps_trading_enabled(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        s.add(_fill("SPY", "BUY", 10, datetime(2026, 10, 28, 14, tzinfo=UTC)))
    with Session(engine) as s, s.begin():
        r = reconcile(s, FakeBroker({"SPY": 10}), "paper")
        assert r.ok and r.summary() == "对账一致"
    with Session(engine) as s:
        assert trading_enabled(s)


@pytest.mark.db
@needs_db
def test_mismatch_halts_trading(engine: Engine) -> None:
    """人为制造不一致（券商侧多出持仓）应被拦截并关闭熔断开关。"""
    with Session(engine) as s, s.begin():
        s.add(_fill("SPY", "BUY", 10, datetime(2026, 10, 28, 14, tzinfo=UTC)))
    with Session(engine) as s, s.begin():
        r = reconcile(s, FakeBroker({"SPY": 10, "GLD": 5}), "paper")
        assert not r.ok
        assert "GLD: 本地 0 股，券商 5 股" in r.summary()
    with Session(engine) as s:
        assert not trading_enabled(s)
        reason = s.get(SystemFlag, "halt_reason")
        assert reason is not None and "GLD" in reason.value


@pytest.mark.db
@needs_db
def test_record_snapshot_idempotent(engine: Engine) -> None:
    acct = AccountSnapshot(D("100"), D("1100"), D("100"), datetime.now(UTC))
    pos = {"SPY": Position("SPY", 2, D("500"), D("1000"))}
    for _ in range(2):
        with Session(engine) as s, s.begin():
            record_snapshot(s, "paper", date(2026, 10, 28), acct, pos)
    with Session(engine) as s:
        assert expected_positions(s, "paper") == {"SPY": 2}
        assert s.query(PositionSnapshot).count() == 1
