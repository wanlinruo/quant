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


# ---- 第 2 批：宏观、新闻、财经日历（#69 第 2 批 Task 7）----

MACRO = {"generated_at": "2026-11-06T21:00:00-05:00", "items": [
    {"symbol": "VIX", "label": "VIX", "close": 17.5, "change": 0.5, "change_pct": 0.03,
     "spark": [17.0, 17.5], "as_of": "2026-11-05", "note": "", "unit": "price"},
    {"symbol": "US10Y", "label": "美债 10 年", "close": 4.98, "change": 0.05, "change_pct": None,
     "spark": [4.93, 4.98], "as_of": "2026-11-05", "note": "", "unit": "yield"}]}  # fmt: skip
MOVER = {"symbol": "NVDA", "name": "NVIDIA", "sector": "信息技术", "market_cap": 4.5e12,
         "close": 190.0, "change_pct": 0.06, "volume_ratio": 2.4, "signals": [],
         "spark": []}  # fmt: skip
REASON = {"headline": "Nvidia extends gains", "source": "Reuters",
          "time": "2026-11-06T18:00:00+00:00", "url": "https://n/1"}  # fmt: skip
NEWS_ITEM = {"time": "2026-11-06T18:00:00+00:00", "headline": "Fed holds rates",
             "source": "Reuters", "url": "https://n/2", "tags": [],
             "category": "macro"}  # fmt: skip
CAL_ITEM = {"time": "2026-11-06T08:30:00-05:00", "date_only": False, "kind": "econ",
            "title": "Non-Farm Employment Change", "impact": 3, "actual": "63K",
            "forecast": "63K", "previous": "29K", "symbols": [], "held": False,
            "session": None}  # fmt: skip


def _put(tmp_path: Path, name: str, doc: object) -> None:
    p = tmp_path / "market" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


def test_overview_includes_macro_even_when_pending(client: TestClient, tmp_path: Path) -> None:
    _put(tmp_path, "macro.json", MACRO)
    body = client.get("/api/market/overview").json()
    assert body["status"] == "pending"
    assert body["macro"][0]["symbol"] == "VIX" and body["macro"][1]["unit"] == "yield"
    assert body["macro"][0]["as_of"] == "2026-11-05"


def test_mover_reason_merged_only_for_same_day(client: TestClient, tmp_path: Path) -> None:
    _put(tmp_path, "snapshot.json", {"as_of": "2026-11-06", "movers": [MOVER]})
    _put(tmp_path, "news.json", {"generated_at": "x", "as_of": "2026-11-06", "items": [],
                                 "reasons": {"NVDA": REASON}})  # fmt: skip
    m = client.get("/api/market/overview").json()["movers"][0]
    assert m["reason"]["headline"] == "Nvidia extends gains"
    _put(tmp_path, "news.json", {"generated_at": "x", "as_of": "2026-11-05", "items": [],
                                 "reasons": {"NVDA": REASON}})  # fmt: skip
    assert client.get("/api/market/overview").json()["movers"][0]["reason"] is None


def test_news_and_calendar_endpoints(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/market/news").json()["status"] == "pending"
    assert client.get("/api/market/calendar").json()["status"] == "pending"
    _put(tmp_path, "news.json", {"generated_at": "2026-11-06T21:00:00-05:00", "as_of": None,
                                 "items": [NEWS_ITEM], "reasons": {}})  # fmt: skip
    _put(tmp_path, "calendar.json", {"generated_at": "2026-11-06T21:00:00-05:00",
                                     "released": [CAL_ITEM], "upcoming": []})  # fmt: skip
    news = client.get("/api/market/news").json()
    assert news["status"] == "ok" and news["items"][0]["category"] == "macro"
    cal = client.get("/api/market/calendar").json()
    assert cal["status"] == "ok" and cal["released"][0]["actual"] == "63K"


def test_broken_files_are_pending(client: TestClient, tmp_path: Path) -> None:
    _put(tmp_path, "news.json", "{broken")
    _put(tmp_path, "calendar.json", {"released": [{"kind": 1}]})
    _put(tmp_path, "macro.json", {"items": [{"symbol": "X"}]})
    _put(tmp_path, "snapshot.json", {"as_of": "2026-11-06", "movers": [MOVER]})
    assert client.get("/api/market/news").status_code == 200
    assert client.get("/api/market/news").json()["status"] == "pending"
    assert client.get("/api/market/calendar").json()["status"] == "pending"
    body = client.get("/api/market/overview").json()
    assert body["status"] == "ok" and body["macro"] == [] and body["movers"][0]["reason"] is None


# ---- 盘中快照（#93）----


def _write_pair(tmp_path: Path) -> None:
    """日线快照截至 10-30；盘中快照为 11-02（周一）。"""
    import pandas as pd

    from quant.data.market_store import MarketStore, intraday_snapshot_path
    from quant.market.board import build_snapshot

    store = MarketStore(tmp_path)
    f = frames()
    for d in IDX:
        day = pd.DataFrame({k: f[k].loc[d] for k in ("open", "high", "low", "close", "volume")})
        day = day.rename_axis("symbol").reset_index()
        day["vwap"] = day["close"]
        store.write_day(d.date(), day, keep=set(day["symbol"]))
    daily = build_snapshot(store, GICS, IDX[-1].date(), stale=False)
    text = json.dumps(daily.to_json(), allow_nan=False)
    snapshot_path(tmp_path).write_text(text, encoding="utf-8")
    prev = f["close"].iloc[-1]
    bar = pd.DataFrame({"open": prev, "high": prev, "low": prev, "close": prev * 1.01})
    from datetime import date

    intra = build_snapshot(store, GICS, date(2026, 11, 2), stale=False, today_bar=bar).to_json()
    intra["intraday_time"] = "2026-11-02T15:10:00+00:00"
    text = json.dumps(intra, allow_nan=False)
    intraday_snapshot_path(tmp_path).write_text(text, encoding="utf-8")


def _at(monkeypatch: pytest.MonkeyPatch, iso: str) -> None:
    from datetime import datetime

    import quant.api.intraday_view as iv

    monkeypatch.setattr(iv, "_now", lambda: datetime.fromisoformat(iso))


def test_overview_prefers_todays_intraday_snapshot(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pair(tmp_path)
    _at(monkeypatch, "2026-11-02T15:20:00+00:00")  # 周一盘中
    o = client.get("/api/market/overview").json()
    assert o["status"] == "ok" and o["intraday"] is True and o["as_of"] == "2026-11-02"
    assert o["intraday_time"].startswith("2026-11-02T15:10")
    _at(monkeypatch, "2026-11-02T23:00:00+00:00")  # 收盘后、夜间日线未生成：仍用今天的盘中快照
    assert client.get("/api/market/overview").json()["intraday"] is True


def test_overview_uses_daily_next_morning_or_when_daily_caught_up(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pair(tmp_path)
    _at(monkeypatch, "2026-11-03T13:00:00+00:00")  # 次日盘前：盘中快照是昨天的
    o = client.get("/api/market/overview").json()
    assert o["intraday"] is False and o["as_of"] == "2026-10-30"


def test_overview_prefers_daily_once_it_catches_up(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pair(tmp_path)
    p = snapshot_path(tmp_path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["as_of"] = "2026-11-02"  # 夜间日线已生成
    p.write_text(json.dumps(doc), encoding="utf-8")
    _at(monkeypatch, "2026-11-03T02:00:00+00:00")  # 11-02 晚上 21:00 ET
    assert client.get("/api/market/overview").json()["intraday"] is False
