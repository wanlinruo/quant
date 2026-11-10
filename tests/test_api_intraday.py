"""盘中行情接口（#93）：常规时段返回当天最新报价；其它时段只返回所处时段与说明。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.db.models import IntradayQuote

OPEN_NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # 美东 10:00


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        for sym, fetched in (
            ("SPY", OPEN_NOW),
            ("QQQ", OPEN_NOW),
            ("OLD", OPEN_NOW - timedelta(days=3)),
        ):
            s.add(
                IntradayQuote(
                    symbol=sym,
                    price=500.0,
                    prev_close=495.0,
                    change_pct=0.0101,
                    quote_time=fetched - timedelta(seconds=20),
                    fetched_at=fetched,
                    source="finnhub",
                )
            )
    eng.dispose()
    _clear()
    yield TestClient(create_app())
    _clear()


def get(client: TestClient, now: datetime, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import quant.api.intraday_view as iv

    monkeypatch.setattr(iv, "_now", lambda: now)
    r = client.get("/api/market/intraday")
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


@needs_db
def test_regular_session_returns_today_quotes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = get(client, OPEN_NOW + timedelta(minutes=1), monkeypatch)
    assert d["session"] == "regular" and d["session_text"] == "盘中"
    assert [q["symbol"] for q in d["quotes"]] == ["QQQ", "SPY"]  # 之前交易日的 OLD 不返回
    assert d["stale"] is False and "仅供查看" in d["note"]


@needs_db
def test_stale_after_twenty_minutes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    d = get(client, OPEN_NOW + timedelta(minutes=21), monkeypatch)
    assert d["stale"] is True and "延迟" in d["note"]


@needs_db
def test_extended_hours_not_connected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    d = get(client, datetime(2026, 11, 2, 22, 0, tzinfo=UTC), monkeypatch)  # 美东 17:00 盘后
    assert d["session"] == "post" and d["quotes"] == []
    assert "扩展时段行情未接入" in d["note"]


def test_without_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import quant.api.intraday_view as iv

    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW)
    _clear()
    d = TestClient(create_app()).get("/api/market/intraday").json()
    _clear()
    assert d["session"] == "regular" and d["quotes"] == [] and "数据库" in d["note"]


def test_database_unreachable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import quant.api.intraday_view as iv

    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QUANT_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW)
    _clear()
    r = TestClient(create_app()).get("/api/market/intraday")
    _clear()
    assert r.status_code == 200 and r.json()["quotes"] == []


@needs_db
def test_quote_from_before_open_is_not_shown(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开盘后首轮可能取到昨天收盘的报价（t 为昨天）：不当作盘中价显示。"""
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add(
            IntradayQuote(
                symbol="HALT",
                price=1.0,
                prev_close=1.0,
                change_pct=0.0,
                quote_time=datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
                fetched_at=OPEN_NOW,
                source="finnhub",
            )
        )
    eng.dispose()
    d = get(client, OPEN_NOW + timedelta(minutes=1), monkeypatch)
    assert "HALT" not in [q["symbol"] for q in d["quotes"]]


@needs_db
def test_no_false_delay_warning_right_after_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db_url: str
) -> None:
    import quant.api.intraday_view as iv

    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setattr(
        iv, "_now", lambda: datetime(2026, 11, 2, 14, 31, tzinfo=UTC)
    )  # 09:31，首轮尚未写入
    _clear()
    d = TestClient(create_app()).get("/api/market/intraday").json()
    _clear()
    assert d["stale"] is False and "采集中" in d["note"]


# ---- 持仓当日盈亏（调仓日从成交价算起）----


def _seed_positions(db_url: str) -> None:
    from decimal import Decimal

    from quant.db.models import Fill, JobRun, PositionSnapshot

    eng = create_engine(db_url)
    t = OPEN_NOW - timedelta(minutes=10)  # 今天 09:50 成交
    with Session(eng) as s, s.begin():
        for sym, price, pc in (
            ("CRWD", 244.96, 250.4),
            ("MSFT", 410.0, 400.0),
            ("AAPL", 200.0, 190.0),
        ):
            s.merge(
                IntradayQuote(
                    symbol=sym,
                    price=price,
                    prev_close=pc,
                    change_pct=price / pc - 1,
                    quote_time=OPEN_NOW,
                    fetched_at=OPEN_NOW,
                    source="finnhub",
                )
            )
        snap = date(2026, 11, 2)
        s.add(
            JobRun(
                mode="paper",
                job_name="daily_report",
                trade_date=snap,
                status="success",
                started_at=t,
                finished_at=t,
            )
        )
        for sym, qty in (("CRWD", 391), ("MSFT", 10), ("AAPL", 5)):
            s.add(
                PositionSnapshot(
                    snapshot_date=snap,
                    mode="paper",
                    symbol=sym,
                    qty=qty,
                    avg_cost=Decimal(1),
                    market_value=Decimal(1),
                )
            )
        s.add(
            PositionSnapshot(
                snapshot_date=snap,
                mode="live",
                symbol="MSFT",
                qty=99,
                avg_cost=Decimal(1),
                market_value=Decimal(1),
            )
        )
        s.add_all(
            [
                Fill(
                    broker_order_id="1",
                    mode="paper",
                    symbol="CRWD",
                    side="BUY",
                    qty=391,
                    price=Decimal("242.306"),
                    commission=Decimal(0),
                    filled_at=t,
                ),
                Fill(
                    broker_order_id="2",
                    mode="paper",
                    symbol="AAPL",
                    side="SELL",
                    qty=5,
                    price=Decimal("195"),
                    commission=Decimal(0),
                    filled_at=t,
                ),
                Fill(
                    broker_order_id="3",
                    mode="paper",
                    symbol="MSFT",
                    side="BUY",
                    qty=3,
                    price=Decimal("405"),
                    commission=Decimal(0),
                    filled_at=t - timedelta(days=3),
                ),
            ]
        )
    eng.dispose()


@needs_db
def test_day_pnl_counts_from_fill_price_on_trade_day(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quant.api.intraday_view as iv

    _seed_positions(db_url)
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW + timedelta(minutes=1))
    d = client.get("/api/market/intraday", params={"mode": "paper"}).json()
    pos = {p["symbol"]: p for p in d["positions"]}
    assert set(pos) == {"CRWD", "MSFT", "AAPL"}  # 只取该账户
    # 今天 242.306 买入：391 × (244.96 − 242.306)，不是按昨收 250.4
    assert (
        pos["CRWD"]["day_pnl"] == pytest.approx(391 * (244.96 - 242.306))
        and pos["CRWD"]["traded_today"]
    )
    # 隔夜持有、今天没成交：按昨收
    assert (
        pos["MSFT"]["day_pnl"] == pytest.approx(10 * (410 - 400))
        and not pos["MSFT"]["traded_today"]
    )
    # 昨天持有 10 股、今天 195 卖出 5 股：5 × (200 − 190) + 5 × (195 − 190)
    assert pos["AAPL"]["day_pnl"] == pytest.approx(5 * 10 + 5 * 5)
    assert d["day_pnl"] == pytest.approx(sum(p["day_pnl"] for p in d["positions"]))


@needs_db
def test_partial_or_stale_position_quotes_never_produce_partial_total(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import delete, update

    import quant.api.intraday_view as iv

    _seed_positions(db_url)
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.execute(delete(IntradayQuote).where(IntradayQuote.symbol == "AAPL"))
        s.execute(
            update(IntradayQuote)
            .where(IntradayQuote.symbol == "MSFT")
            .values(quote_time=OPEN_NOW - timedelta(minutes=25))
        )
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW + timedelta(minutes=1))
    d = client.get("/api/market/intraday?mode=paper").json()
    assert not d["complete"] and d["day_pnl"] is None and d["market_value"] is None
    assert d["missing_symbols"] == ["AAPL"] and d["stale_symbols"] == ["MSFT"]
    assert len(d["positions"]) == 3
    assert next(p for p in d["positions"] if p["symbol"] == "CRWD")["day_pnl"] is not None
    eng.dispose()


@needs_db
def test_fully_exited_position_is_included_in_day_pnl(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    import quant.api.intraday_view as iv
    from quant.db.models import Fill

    _seed_positions(db_url)
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add(
            Fill(
                mode="paper",
                broker_order_id="exit",
                symbol="QQQ",
                side="SELL",
                qty=10,
                price=Decimal("505"),
                commission=Decimal(0),
                filled_at=OPEN_NOW,
            )
        )
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW + timedelta(minutes=1))
    d = client.get("/api/market/intraday?mode=paper").json()
    sold = next(p for p in d["positions"] if p["symbol"] == "QQQ")
    assert sold["qty"] == 0 and sold["day_pnl"] == 100  # 10 × (505 − 495)
    assert d["complete"] and d["day_pnl"] == pytest.approx(
        sum(p["day_pnl"] for p in d["positions"])
    )
    eng.dispose()


@needs_db
def test_previous_day_positions_apply_todays_fills_once(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    import quant.api.intraday_view as iv
    from quant.db.models import Fill, PositionSnapshot

    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add(
            PositionSnapshot(
                mode="paper",
                snapshot_date=date(2026, 10, 30),
                symbol="SPY",
                qty=10,
                avg_cost=Decimal(400),
                market_value=Decimal(4950),
            )
        )
        s.add(
            Fill(
                mode="paper",
                broker_order_id="reduce",
                symbol="SPY",
                side="SELL",
                qty=4,
                price=Decimal(498),
                commission=Decimal(0),
                filled_at=OPEN_NOW,
            )
        )
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW + timedelta(minutes=1))
    d = client.get("/api/market/intraday?mode=paper").json()
    assert d["positions"][0]["qty"] == 6
    assert d["day_pnl"] == 6 * 5 + 4 * 3
    assert d["market_value"] == 3000
    eng.dispose()


@needs_db
def test_unreconciled_or_old_snapshot_cannot_be_estimated(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import update

    import quant.api.intraday_view as iv
    from quant.db.models import JobRun, PositionSnapshot

    _seed_positions(db_url)
    monkeypatch.setattr(iv, "_now", lambda: OPEN_NOW + timedelta(minutes=1))
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.execute(update(JobRun).values(status="running"))
    d = client.get("/api/market/intraday?mode=paper").json()
    assert not d["complete"] and not d["positions"] and "对账尚未完成" in d["note"]
    with Session(eng) as s, s.begin():
        s.execute(update(PositionSnapshot).values(snapshot_date=date(2026, 10, 29)))
    d = client.get("/api/market/intraday?mode=paper").json()
    assert not d["complete"] and "快照过旧" in d["note"]
    eng.dispose()
