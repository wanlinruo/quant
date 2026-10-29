"""Executor：用假的券商与时钟测试完整流程。需要 PostgreSQL（QUANT_TEST_DATABASE_URL），否则跳过。"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from fakes import T0, Clock, FakeBroker, Notes
from quant.core.models import (
    OrderRequest,
    Side,
    make_client_order_id,
)
from quant.core.safety import OrderNotAllowedError
from quant.core.settings import Settings
from quant.db.models import Fill, Order, PositionSnapshot, Signal, SystemFlag
from quant.engine.executor import ExecutionError, Executor
from quant.engine.reconcile import expected_positions, trading_enabled
from quant.notify.base import Level

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL"),
]
D = Decimal
DAY = date(2026, 10, 29)  # 周四
PREV = date(2026, 10, 28)
STRATEGY = "etf_momentum"


def settings(**risk: object) -> Settings:
    r = {"symbol_whitelist": ["SPY", "QQQ", "TLT", "GLD", "SHY"], "max_order_usd": "150000"}
    return Settings(broker="tiger", risk={**r, **risk})


@pytest.fixture(autouse=True)
def paper_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # 交易模式以环境变量为准（优先于构造参数）
    monkeypatch.setenv("TRADING_MODE", "paper")


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(DB_URL)
    with Session(eng) as s, s.begin():
        s.get(SystemFlag, "trading_enabled").value = "true"  # type: ignore[union-attr]
        # 前一交易日：持有 SPY 160 股；信号切换到 QQQ
        s.add(PositionSnapshot(snapshot_date=PREV, mode="paper", symbol="SPY", qty=160,
                               avg_cost=D("550"), market_value=D("96000")))  # fmt: skip
        s.add(Signal(strategy=STRATEGY, signal_date=PREV, target_weights={"QQQ": 1.0},
                     data_as_of=PREV))  # fmt: skip
    yield eng
    eng.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def broker(clock: Clock) -> FakeBroker:
    return FakeBroker(clock, "2000", {"SPY": 160})


def make(engine: Engine, broker: FakeBroker, clock: Clock, notes: Notes | None = None,
         **risk: object) -> Executor:  # fmt: skip
    return Executor(engine, broker, settings(**risk), notes or Notes(), STRATEGY,
                    now=clock.now, sleep=clock.sleep)  # fmt: skip


def orders(engine: Engine) -> list[Order]:
    with Session(engine) as s:
        return list(s.scalars(select(Order).order_by(Order.id)))


def halted(engine: Engine) -> bool:
    with Session(engine) as s:
        return not trading_enabled(s)


# ---- 正常流程 ----


def test_rotation_sell_then_buy(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    notes = Notes()
    report = make(engine, broker, clock, notes).run(DAY)
    assert report.status == "done"
    assert [(o.side, o.symbol, o.qty) for o in broker.placed] == [
        (Side.SELL, "SPY", 160), (Side.BUY, "QQQ", 191),
    ]  # fmt: skip
    sell, buy = broker.placed
    assert sell.limit_price == D("598.80") and buy.limit_price == D("501.00")
    assert sell.client_order_id == f"{DAY}-{STRATEGY}-SPY-SELL-1"
    assert broker.pos == {"SPY": 0, "QQQ": 191}
    assert broker.cash > 0  # 不用保证金
    rows = orders(engine)
    assert [r.status for r in rows] == ["FILLED", "FILLED"]
    assert rows[0].reference_price == D("600") and rows[0].broker_order_id == "1001"
    with Session(engine) as s:
        assert len(s.scalars(select(Fill)).all()) == 2
        assert expected_positions(s, "paper") == {"QQQ": 191}  # 本地记录与券商一致
    assert notes.sent[-1][2] is Level.INFO and "[paper]" in notes.sent[-1][0]


def test_rerun_same_day_places_nothing(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    make(engine, broker, clock).run(DAY)
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "no_orders"
    assert len(broker.placed) == 2


def test_timeout_then_retry(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    broker.behavior["QQQ"] = ["hang", "hang", "fill"]
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "done"
    buys = [o for o in broker.placed if o.side is Side.BUY]
    assert [o.client_order_id[-1] for o in buys] == ["1", "2", "3"]
    assert len(broker.cancelled) == 2
    assert [r.status for r in orders(engine)] == ["FILLED", "CANCELLED", "CANCELLED", "FILLED"]
    # 超时 5 分钟才撤单
    assert clock.now() - T0 >= timedelta(minutes=10)


def test_partial_fill_retries_remaining(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    broker.behavior["SPY"] = ["partial", "fill"]
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "done"
    sells = [o for o in broker.placed if o.side is Side.SELL]
    assert [o.qty for o in sells] == [160, 80]
    assert broker.pos["SPY"] == 0
    with Session(engine) as s:
        assert expected_positions(s, "paper") == {"QQQ": broker.pos["QQQ"]}


def test_no_orders_when_on_target(engine: Engine, clock: Clock) -> None:
    with Session(engine) as s, s.begin():
        s.query(Signal).delete()
        s.add(Signal(strategy=STRATEGY, signal_date=PREV, target_weights={"SPY": 1.0},
                     data_as_of=PREV))  # fmt: skip
    broker = FakeBroker(clock, "2000", {"SPY": 160})
    assert make(engine, broker, clock).run(DAY).status == "no_orders"
    assert broker.placed == []


# ---- 停止条件 ----


def test_live_mode_refused(engine: Engine, broker: FakeBroker, clock: Clock,
                           monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setenv("TRADING_MODE", "live")
    s = settings()
    assert s.trading_mode == "live"
    with pytest.raises(OrderNotAllowedError):
        Executor(engine, broker, s, Notes(), STRATEGY)


def test_breaker_off(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    with Session(engine) as s, s.begin():
        s.get(SystemFlag, "trading_enabled").value = "false"  # type: ignore[union-attr]
    notes = Notes()
    assert make(engine, broker, clock, notes).run(DAY).status == "halted"
    assert broker.placed == [] and notes.sent[-1][2] is Level.ERROR


@pytest.mark.parametrize(
    "when",
    [datetime(2026, 10, 29, 13, 0, tzinfo=UTC),  # 09:00 ET 盘前
     datetime(2026, 10, 29, 19, 30, tzinfo=UTC)],  # 15:30 ET，收盘前 30 分钟内
)  # fmt: skip
def test_outside_session(engine: Engine, broker: FakeBroker, when: datetime) -> None:
    clock = Clock(when)
    with pytest.raises(ExecutionError, match="不在可下单时段"):
        make(engine, broker, clock).run(DAY)
    assert broker.placed == []


def test_half_day_deadline(engine: Engine, clock: Clock) -> None:
    # 2026-11-27 感恩节次日 13:00 收盘，12:40 ET 已过截止时间
    broker = FakeBroker(Clock(datetime(2026, 11, 27, 17, 40, tzinfo=UTC)), "0", {})
    with pytest.raises(ExecutionError, match="12:30"):
        make(engine, broker, broker.clock).run(date(2026, 11, 27))


def test_missing_signal(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    with Session(engine) as s, s.begin():
        s.query(Signal).delete()
    with pytest.raises(ExecutionError, match="信号"):
        make(engine, broker, clock).run(DAY)


def test_stale_signal_rejected(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    with Session(engine) as s, s.begin():
        s.scalars(select(Signal)).one().data_as_of = date(2026, 10, 27)
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "rejected" and "stale" in report.message
    assert broker.placed == []


def test_target_not_whitelisted(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    with Session(engine) as s, s.begin():
        s.scalars(select(Signal)).one().target_weights = {"TSLA": 1.0}
    assert make(engine, broker, clock).run(DAY).status == "rejected"
    assert broker.placed == []


def test_order_cap_rejects_whole_batch(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    report = make(engine, broker, clock, max_order_usd="90000").run(DAY)
    assert report.status == "rejected" and "order_usd" in report.message
    assert broker.placed == []  # 卖单也没有提交


def test_turnover_rejects(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    report = make(engine, broker, clock, max_daily_turnover=1.5).run(DAY)
    assert report.status == "rejected" and "turnover" in report.message
    assert broker.placed == []


def test_stale_quote(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    broker.quote_time = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)  # 昨日收盘价
    with pytest.raises(ExecutionError, match="早于今日开盘"):
        make(engine, broker, clock).run(DAY)
    assert broker.placed == []


def test_reconcile_mismatch_halts(engine: Engine, clock: Clock) -> None:
    broker = FakeBroker(clock, "2000", {"SPY": 150})
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "halted" and "SPY" in report.message
    assert halted(engine) and broker.placed == []


def test_unlisted_holding(engine: Engine, clock: Clock) -> None:
    broker = FakeBroker(clock, "2000", {"SPY": 160})
    broker.prices["AAPL"] = D("200")
    with Session(engine) as s, s.begin():
        s.add(PositionSnapshot(snapshot_date=PREV, mode="paper", symbol="AAPL", qty=1,
                               avg_cost=D("1"), market_value=D("1")))  # fmt: skip
    broker.pos["AAPL"] = 1
    with pytest.raises(ExecutionError, match="白名单外"):
        make(engine, broker, clock).run(DAY)
    assert broker.placed == []


def test_retries_exhausted(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    broker.behavior["QQQ"] = ["hang"] * 4
    notes = Notes()
    with pytest.raises(ExecutionError, match="重试 3 次"):
        make(engine, broker, clock, notes).run(DAY)
    assert len([o for o in broker.placed if o.symbol == "QQQ"]) == 4  # 首次 + 重试 3 次
    assert broker.get_open_orders() == []
    assert all(r.status in ("FILLED", "CANCELLED") for r in orders(engine))
    assert notes.sent[-1][2] is Level.ERROR


def test_abort_records_partial_fill(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    # 买单部分成交后一直挂着，重试用尽；撤单后部分成交必须入库，日报对账才能一致
    broker.behavior["QQQ"] = ["partial", "hang", "hang", "hang"]
    with pytest.raises(ExecutionError):
        make(engine, broker, clock).run(DAY)
    with Session(engine) as s:
        assert expected_positions(s, "paper") == {s_: q for s_, q in broker.pos.items() if q}


def test_rejected_stops(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    broker.behavior["SPY"] = ["reject"]
    with pytest.raises(ExecutionError, match="拒绝"):
        make(engine, broker, clock).run(DAY)
    assert [o.side for o in broker.placed] == [Side.SELL]  # 不继续买入
    assert orders(engine)[0].status == "REJECTED"


def test_unknown_submit_result_halts(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    broker.behavior["SPY"] = ["error"]
    with pytest.raises(ExecutionError, match="结果未知"):
        make(engine, broker, clock).run(DAY)
    assert halted(engine)
    row = orders(engine)[0]
    assert row.status == "SUBMITTED" and row.broker_order_id is None


def test_unknown_local_order_halts_next_run(engine: Engine, broker: FakeBroker,
                                            clock: Clock) -> None:  # fmt: skip
    with Session(engine) as s, s.begin():
        s.add(Order(client_order_id=make_client_order_id(DAY, STRATEGY, "SPY", Side.SELL, 1),
                    mode="paper", symbol="SPY", side="SELL", order_type="LMT", qty=160,
                    limit_price=D("598.80"), status="SUBMITTED"))  # fmt: skip
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "halted" and "提交结果未知" in report.message
    assert halted(engine) and broker.placed == []


# ---- 重启恢复 ----


def test_recover_cancels_own_open_order(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    # 上次运行挂出卖单后崩溃（数据库没来得及写券商订单号），成交了一半
    cid = make_client_order_id(DAY, STRATEGY, "SPY", Side.SELL, 1)
    with Session(engine) as s, s.begin():
        s.add(Order(client_order_id=cid, mode="paper", symbol="SPY", side="SELL",
                    order_type="LMT", qty=160, limit_price=D("598.80"),
                    status="SUBMITTED"))  # fmt: skip
    broker.behavior["SPY"] = ["partial"]
    broker.place_order(OrderRequest(cid, "SPY", Side.SELL, 160, limit_price=D("598.80")))
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "done"
    assert "1001" in broker.cancelled
    sells = [o for o in broker.placed if o.side is Side.SELL]
    assert [o.client_order_id[-1] for o in sells] == ["1", "2"]  # 不重复使用订单号
    assert sells[1].qty == 80
    assert broker.pos == {"SPY": 0, "QQQ": broker.pos["QQQ"]}
    with Session(engine) as s:
        assert expected_positions(s, "paper") == {"QQQ": broker.pos["QQQ"]}


def test_foreign_open_order_halts(engine: Engine, broker: FakeBroker, clock: Clock) -> None:
    cid = make_client_order_id(date(2026, 10, 28), STRATEGY, "TLT", Side.BUY, 1)
    broker.behavior["TLT"] = ["hang"]
    broker.place_order(OrderRequest(cid, "TLT", Side.BUY, 10, limit_price=D("90")))
    report = make(engine, broker, clock).run(DAY)
    assert report.status == "halted" and "非本次运行" in report.message
    assert halted(engine) and broker.cancelled == []
