"""首页市场看板接口（#70 第 1 批 Task 5）：只读快照文件。"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.data.market_store import snapshot_path
from test_market_board import GICS, IDX, frames


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    _clear()
    yield TestClient(create_app())
    _clear()


def test_overview_pending_without_snapshot(client: TestClient) -> None:
    r = client.get("/api/market/overview")
    assert r.status_code == 200
    assert r.json()["status"] == "pending" and r.json()["strip"] == []


def test_overview_reads_snapshot(client: TestClient, tmp_path: Path) -> None:
    import pandas as pd

    from quant.data.market_store import MarketStore
    from quant.market.board import build_snapshot

    store = MarketStore(tmp_path)
    f = frames()
    for d in IDX:
        day = pd.DataFrame({k: f[k].loc[d] for k in ("open", "high", "low", "close", "volume")})
        day = day.rename_axis("symbol").reset_index()
        day["vwap"] = day["close"]
        store.write_day(d.date(), day, keep=set(day["symbol"]))
    snap = build_snapshot(store, GICS, IDX[-1].date(), stale=True)
    path = snapshot_path(tmp_path)
    path.write_text(json.dumps(snap.to_json(), allow_nan=False), encoding="utf-8")
    o = client.get("/api/market/overview").json()
    assert o["status"] == "ok" and o["stale"] is True and o["as_of"] == "2026-10-30"
    assert [x["symbol"] for x in o["strip"]] == ["SPY", "QQQ", "DIA", "IWM", "GLD"]
    assert o["breadth"]["advancers"]["value"] == 3
    assert len(o["sectors"]) == 11 and o["highlights"].startswith("标普 500")
    assert set(o["heatmap"]) == {"1D", "1W", "1M", "YTD"}


def test_overview_corrupt_snapshot_is_pending(client: TestClient, tmp_path: Path) -> None:
    path = snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    r = client.get("/api/market/overview")
    assert r.status_code == 200 and r.json()["status"] == "pending"
