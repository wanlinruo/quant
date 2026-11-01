"""个股页接口与日线回落（#65 Task 6）。带 db 标记的需要 QUANT_TEST_DATABASE_URL。"""

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from api_fixtures import SEED_FIRST_DAY, SEED_LAST_DAY, needs_db, seed, write_sample_bars
from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.data.events import live_earnings_path, save_earnings
from quant.data.store import ParquetStore
from quant.data.universe import constituents_path
from quant.data.universe_bars import universe_store
from quant.db.models import Signal

DETAIL: dict[str, Any] = {
    "sleeves": {"momentum": {"META": 0.5}, "swing": {"NVDA": 1.0}},
    "sleeve_weights": {"momentum": 0.4, "swing": 0.6}, "timing_factor": 1.0,
    "vol_symbol": "QQQ", "vol_20d": 0.2, "vol_threshold": 0.3,
    "momentum_rank": ["META", "AMD", "NVDA"], "universe_size": 503,
    "rebalance_day": "2026-09-30", "top_n": 10,
}  # fmt: skip


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


def _trading_days(store: ParquetStore, symbol: str) -> list[date]:
    return [pd.Timestamp(t).date() for t in store.read_bars(symbol).index]


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    eng = create_engine(db_url)
    seed(eng, "paper")
    with Session(eng) as s, s.begin():
        s.execute(update(Signal).values(detail=DETAIL))
        s.add(Signal(mode="paper", strategy="combo", signal_date=SEED_FIRST_DAY,
                     data_as_of=SEED_FIRST_DAY, target_weights={"META": 0.4},
                     detail={**DETAIL, "momentum_rank": ["NVDA", "META"]}))  # fmt: skip
    eng.dispose()
    store = ParquetStore(tmp_path)
    for sym in ("NVDA", "META", "QQQ", "SPY"):
        write_sample_bars(store, sym)
    days = _trading_days(store, "NVDA")
    future = days[-1] + timedelta(days=20)
    save_earnings(live_earnings_path(tmp_path), {"NVDA": [days[-90], days[-30], future]})
    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('date,tickers\n2020-01-02,"NVDA,AAPL"\n', encoding="utf-8")
    _clear()
    yield TestClient(create_app())
    _clear()


@pytest.mark.db
@needs_db
def test_stock_profile_held_symbol(client: TestClient) -> None:
    p = client.get("/api/stock/NVDA/profile", params={"mode": "paper"}).json()
    assert p["symbol"] == "NVDA" and p["quote"]["close"] > 0
    assert p["quote"]["high_52w"] >= p["quote"]["close"] >= p["quote"]["low_52w"]
    rel = p["relation"]
    assert rel["held"] and rel["sleeve"] == "swing"
    assert rel["weight"] == pytest.approx(60000 / 101000)
    assert rel["momentum_rank"] == 3 and rel["universe_size"] == 503
    assert rel["in_sp500"] and rel["swing_universe"]
    assert rel["prev_holding"] is None and rel["next_holding"] == "META"
    assert set(p["periods"]) == {"1D", "1W", "1M", "3M", "6M", "YTD", "1Y"}
    assert set(p["periods_qqq"]) == set(p["periods"])
    tech = p["technicals"]
    assert tech["rsi14"] is not None and tech["ma120"] is not None and tech["dist_ma10"] > 0
    assert p["swing"]["trend_up"] is True and p["swing"]["above_support"] is True
    assert p["key_stats"]["next_earnings"] is not None and p["key_stats"]["days_to_earnings"] > 0
    assert len(p["earnings"]["history"]) == 2
    assert p["earnings"]["held_through"] is True
    assert p["holding_periods"] == [
        {"start": SEED_LAST_DAY.isoformat(), "end": SEED_LAST_DAY.isoformat()}
    ]
    assert [t["symbol"] for t in p["trades"]] == ["NVDA"]
    assert [r["rank"] for r in p["rank_history"]] == [1, 3]  # 按日期升序


@pytest.mark.db
@needs_db
def test_rank_history_by_mode(client: TestClient) -> None:
    p = client.get("/api/stock/NVDA/profile", params={"mode": "live"}).json()
    assert p["rank_history"] == [] and p["relation"]["held"] is False and p["trades"] == []
    assert p["quote"] is not None  # 行情不跟随账户


@pytest.mark.db
@needs_db
def test_earnings_history_moves(client: TestClient, tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    close = store.read_bars("NVDA")["close"]
    days = _trading_days(store, "NVDA")
    p = client.get("/api/stock/NVDA/profile", params={"mode": "paper"}).json()
    moves = {m["date"]: m["move"] for m in p["earnings"]["history"]}
    want = float(close.iloc[-30] / close.iloc[-31] - 1)
    assert moves[days[-30].isoformat()] == pytest.approx(want)


@pytest.mark.db
@needs_db
def test_stock_profile_unknown_symbol(client: TestClient) -> None:
    r = client.get("/api/stock/ZZZZ/profile", params={"mode": "paper"})
    assert r.status_code == 200
    p = r.json()
    assert (
        p["quote"] is None and p["technicals"]["rsi14"] is None and p["earnings"]["history"] == []
    )
    assert p["relation"]["held"] is False


@pytest.mark.db
@needs_db
def test_stock_profile_invalid_symbol(client: TestClient) -> None:
    r = client.get("/api/stock/%5EVIX/profile", params={"mode": "paper"})
    assert r.status_code == 200
    assert r.json()["quote"] is None and r.json()["trades"] == []


def test_bars_fallback_to_universe_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    write_sample_bars(universe_store(tmp_path), "AMD")
    write_sample_bars(ParquetStore(tmp_path), "SPY")
    _clear()
    c = TestClient(create_app())
    amd = c.get("/api/market/bars/AMD").json()
    assert amd["adjusted"] is False and len(amd["bars"]) > 100
    spy = c.get("/api/market/bars/SPY").json()
    assert spy["adjusted"] is True and len(spy["bars"]) > 100
    assert c.get("/api/market/bars/ZZZZ").json() == {"adjusted": True, "bars": []}
    _clear()
