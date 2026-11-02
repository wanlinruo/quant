"""盘中行情接口（#93）：常规时段返回当天最新报价；其它时段只返回所处时段与说明。"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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
def test_stale_after_five_minutes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    d = get(client, OPEN_NOW + timedelta(minutes=10), monkeypatch)
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
