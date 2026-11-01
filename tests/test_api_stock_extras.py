"""个股页估值、新闻、同板块接口（#70 第 3 批 Task 4）：只读文件，不需要数据库。"""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.data import market_store as ms
from quant.market.stock_job import METRIC_KEYS

SUB = "Technology Hardware"
ROWS = [("AAPL", SUB), ("DELL", SUB), ("HPQ", SUB), ("SMCI", SUB),
        ("MSFT", "Software"), ("NVDA", "Semis"), ("ORCL", "Software"), ("ADBE", "Software"),
        ("CRM", "Software"), ("XOM", "Oil")]  # fmt: skip
CAPS = {"AAPL": 5e12, "DELL": 9e10, "HPQ": 3e10, "SMCI": 2e10, "MSFT": 4e12, "NVDA": 4.5e12,
        "ORCL": 6e11, "ADBE": 2e11, "CRM": 2.5e11, "XOM": 5e11}  # fmt: skip
PERIODS = {"1D": 0.012, "1W": 0.02, "1M": 0.05, "3M": 0.1, "6M": 0.15, "YTD": 0.2, "1Y": 0.25}


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


def setup(data_dir: Path, snapshot: dict[str, object] | None = None) -> None:
    p = ms.gics_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        f'{s},Co {s},{"Energy" if s == "XOM" else "Information Technology"},"{sub}"\n'
        for s, sub in ROWS
    )
    p.write_text("Symbol,Security,GICS Sector,GICS Sub-Industry\n" + lines, encoding="utf-8")
    ms.MarketStore(data_dir).write_caps(
        {s: ms.CapInfo(c, f"Co {s}", date(2026, 10, 30)) for s, c in CAPS.items()}
    )
    if snapshot is None:

        def cell(s: str, r: float) -> dict[str, object]:
            return {
                "symbol": s,
                "name": None,
                "sector": "信息技术",
                "market_cap": CAPS[s],
                "ret": r,
            }

        snapshot = {"as_of": "2026-10-30",
                    "heatmap": {"1D": [cell(s, 0.01) for s, _ in ROWS],
                                "YTD": [cell(s, 0.3) for s, _ in ROWS]},
                    "sector_periods": {"XLK": PERIODS}}  # fmt: skip
    ms.snapshot_path(data_dir).write_text(json.dumps(snapshot), encoding="utf-8")
    sp = ms.stock_path(data_dir, "AAPL")
    assert sp is not None
    sp.parent.mkdir(parents=True, exist_ok=True)
    metrics = {k: 2.0 for k in METRIC_KEYS} | {"roeTTM": 137.18, "grossMarginTTM": None}
    news = [{"time": "2026-10-30T20:10:00+00:00", "headline": "Apple Q4 earnings beat",
             "source": "CNBC", "url": "https://n/1", "tags": ["AAPL"],
             "category": "earnings"}]  # fmt: skip
    sp.write_text(json.dumps({"symbol": "AAPL", "news_updated": "2026-10-30T21:15:00-04:00",
                              "news": news, "metrics_updated": "2026-10-30",
                              "metrics": metrics}), encoding="utf-8")  # fmt: skip


def test_extras_covered_symbol(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    r = client.get("/api/stock/aapl/extras")
    assert r.status_code == 200
    d = r.json()
    assert d["covered"] and d["symbol"] == "AAPL" and d["name"] == "Co AAPL"
    assert d["sector"] == "信息技术" and d["sub_industry"] == SUB and d["sector_etf"] == "XLK"
    assert d["market_cap"] == 5e12 and d["sector_periods"]["1D"] == 0.012
    peers = [p["symbol"] for p in d["peers"]]
    assert peers[:3] == ["DELL", "HPQ", "SMCI"]  # 同子行业优先，按市值降序
    assert "AAPL" not in peers and "XOM" not in peers and len(peers) == 8
    assert peers[3:] == ["NVDA", "MSFT", "ORCL", "CRM", "ADBE"]  # 再用同板块按市值补到 8 只
    assert d["peers"][0]["d1"] == 0.01 and d["peers"][0]["ytd"] == 0.3
    assert d["news"][0]["headline"] == "Apple Q4 earnings beat"
    assert d["news_updated"].startswith("2026-10-30T21:15") and d["metrics_updated"] == "2026-10-30"


def test_extras_metrics_scaled_and_null(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    m = {x["key"]: x for x in client.get("/api/stock/AAPL/extras").json()["metrics"]}
    assert list(m) == list(METRIC_KEYS)
    assert m["roeTTM"]["value"] == pytest.approx(1.3718) and m["roeTTM"]["kind"] == "pct"
    assert m["grossMarginTTM"]["value"] is None
    assert m["peTTM"]["value"] == 2.0 and m["peTTM"]["label"] == "市盈率（TTM）"


def test_extras_uncovered_symbol(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    r = client.get("/api/stock/QQQ/extras")
    assert r.status_code == 200
    d = r.json()
    assert d["covered"] is False and d["peers"] == [] and d["metrics"] == [] and d["news"] == []
    assert d["sector"] is None


def test_extras_rejects_bad_symbol(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    outside = tmp_path / "market" / "x.json"
    outside.write_text(json.dumps({"news": [{"headline": "leak"}]}), encoding="utf-8")
    for path in (
        "/api/stock/..%2Fx/extras",
        "/api/stock/A%20B/extras",
        "/api/stock/ABCDEFGHIJKL/extras",
    ):
        r = client.get(path)
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert r.json()["covered"] is False and r.json()["news"] == []


def test_extras_old_snapshot(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path, snapshot={"as_of": "2026-10-30"})
    d = client.get("/api/stock/AAPL/extras").json()
    assert d["sector_periods"] == {} and d["peers"][0]["d1"] is None
    ms.snapshot_path(tmp_path).unlink()
    d = client.get("/api/stock/AAPL/extras").json()
    assert d["covered"] and d["sector_periods"] == {} and len(d["peers"]) == 8


def test_extras_peers_fill_from_sector_when_few(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    d = client.get("/api/stock/MSFT/extras").json()  # Software 子行业有 4 只（MSFT 之外 3 只）
    assert [p["symbol"] for p in d["peers"]][:3] == ["ORCL", "CRM", "ADBE"]
    d = client.get("/api/stock/NVDA/extras").json()  # Semis 只有自己 → 全部用同板块补
    assert [p["symbol"] for p in d["peers"]][:2] == ["AAPL", "MSFT"]


def test_extras_bad_news_item_does_not_drop_rest(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    sp = ms.stock_path(tmp_path, "AAPL")
    assert sp is not None
    d = json.loads(sp.read_text(encoding="utf-8"))
    d["news"].append({"time": "2026-10-30T14:60:00+00:00", "headline": "bad"})
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = client.get("/api/stock/AAPL/extras").json()
    assert [n["headline"] for n in out["news"]] == ["Apple Q4 earnings beat"]
    assert len(out["metrics"]) == len(METRIC_KEYS)


def test_extras_corrupt_caps_and_snapshot_values(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    (tmp_path / "market" / "caps.json").write_text("{broken", encoding="utf-8")
    r = client.get("/api/stock/AAPL/extras")
    assert r.status_code == 200 and r.json()["covered"] and r.json()["market_cap"] is None
    assert len(r.json()["peers"]) == 8
    setup(tmp_path, snapshot={"as_of": "2026-10-30",
                              "heatmap": {"1D": [{"symbol": "DELL", "ret": "x"}]},
                              "sector_periods": {"XLK": {"1D": "bad", "1W": 0.02}}})  # fmt: skip
    r = client.get("/api/stock/AAPL/extras")
    assert r.status_code == 200
    d = r.json()
    assert d["sector_periods"] == {"1W": 0.02} and d["peers"][0]["d1"] is None


def test_extras_holding_outside_sp500(client: TestClient, tmp_path: Path) -> None:
    setup(tmp_path)
    sp = ms.stock_path(tmp_path, "TSM")
    assert sp is not None
    sp.write_text(json.dumps({"symbol": "TSM", "news_updated": "2026-10-30T21:15:00-04:00",
                              "news": [], "metrics": {}}), encoding="utf-8")  # fmt: skip
    d = client.get("/api/stock/TSM/extras").json()
    assert d["covered"] and d["in_sp500"] is False and d["peers"] == []
    assert client.get("/api/stock/AAPL/extras").json()["in_sp500"] is True
