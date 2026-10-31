"""面板接口路由。带 db 标记的测试需要 QUANT_TEST_DATABASE_URL。"""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from api_fixtures import needs_db, seed, valid_submit_body
from quant.api.app import create_app
from quant.api.backtests import build_submission
from quant.api.deps import get_engine, get_settings
from quant.api.schemas import BacktestSubmit, CostsIn
from quant.core.settings import CostSettings
from quant.db.models import BacktestRun


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client_no_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.delenv("TIGER_PROPS_PATH", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    _clear()
    yield TestClient(create_app())
    _clear()


@pytest.fixture
def client_db(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    eng = create_engine(db_url)
    seed(eng, "paper")
    eng.dispose()
    _clear()
    yield TestClient(create_app())
    _clear()


def test_health_and_meta_without_db(client_no_db: TestClient) -> None:
    assert client_no_db.get("/api/health").json() == {"status": "ok"}
    meta = client_no_db.get("/api/meta").json()
    assert meta["database_configured"] is False
    assert meta["trading_mode"] == "sim"
    assert meta["default_account"] == "sim"
    assert [a["mode"] for a in meta["accounts"]] == ["sim", "paper", "live"]
    assert not any(a["has_data"] for a in meta["accounts"])
    assert "database_url" not in json.dumps(meta)


@pytest.mark.parametrize(
    "path",
    [
        "/api/portfolio/paper/positions",
        "/api/portfolio/paper/curve",
        "/api/signals/latest",
        "/api/backtests",
        "/api/system/jobs",
        "/api/system/queue",
        "/api/system/flags",
    ],
)
def test_endpoints_without_db_return_503(client_no_db: TestClient, path: str) -> None:
    r = client_no_db.get(path)
    assert r.status_code == 503
    assert r.json()["detail"] == "未配置数据库（QUANT_DATABASE_URL）"


@pytest.mark.db
@needs_db
def test_invalid_mode_is_422(client_db: TestClient) -> None:
    assert client_db.get("/api/portfolio/foo/positions").status_code == 422


@pytest.mark.db
@needs_db
def test_equity_rejects_more_than_five(client_db: TestClient) -> None:
    r = client_db.get("/api/backtests/equity", params={"ids": [1, 2, 3, 4, 5, 6]})
    assert r.status_code == 422
    assert client_db.get("/api/backtests/equity", params={"ids": [1]}).json() == []


def test_defaults_without_db(client_no_db: TestClient) -> None:
    d = client_no_db.get("/api/backtests/defaults").json()
    assert "etf_momentum" in d["strategies"] and "combo" in d["strategies"]
    assert d["commission_per_share"] == "0.0039"
    assert d["start"] < d["end"]


def test_market_endpoints_without_data(client_no_db: TestClient) -> None:
    assert client_no_db.get("/api/market/data-status").json() == []
    assert client_no_db.get("/api/market/bars/SPY").json() == []


@pytest.mark.db
@needs_db
def test_portfolio_and_meta_with_data(client_db: TestClient) -> None:
    pos = client_db.get("/api/portfolio/paper/positions").json()
    assert {p["symbol"] for p in pos} == {"NVDA", "META"}
    assert client_db.get("/api/portfolio/live/positions").json() == []
    assert len(client_db.get("/api/portfolio/paper/orders", params={"limit": 1}).json()) == 1
    assert client_db.get("/api/portfolio/paper/fills").json()[0]["symbol"] == "NVDA"
    assert client_db.get("/api/signals/latest").json()[0]["strategy"] == "combo"
    meta = client_db.get("/api/meta").json()
    assert meta["database_configured"] is True
    assert meta["default_account"] == "paper"
    assert [a["has_data"] for a in meta["accounts"]] == [False, True, False]
    assert meta["data_as_of"] is not None


@pytest.mark.db
@needs_db
def test_system_endpoints_with_data(client_db: TestClient) -> None:
    assert client_db.get("/api/system/queue").json() == {
        "queued": 1, "running": 0, "done": 0, "failed": 1,
    }  # fmt: skip
    assert client_db.get("/api/system/flags").json()["trading_enabled"] == "false"
    jobs = {j["job_name"] for j in client_db.get("/api/system/jobs").json()}
    assert jobs == {"daily_data", "daily_signal"}
    assert len(client_db.get("/api/system/failures").json()) == 2


@pytest.mark.db
@needs_db
def test_submit_backtest_queues_run(client_db: TestClient, db_url: str) -> None:
    r = client_db.post("/api/backtests", json=valid_submit_body())
    assert r.status_code == 201
    eng = create_engine(db_url)
    with Session(eng) as s:
        run = s.get(BacktestRun, r.json()["id"])
        assert run is not None and run.status == "queued"
        assert run.params["_costs"]["commission_per_share"] == "0.005"
        assert run.params["_cash"] == "100000"
    eng.dispose()


@pytest.mark.db
@needs_db
def test_submit_invalid_does_not_insert(client_db: TestClient, db_url: str) -> None:
    body = valid_submit_body() | {
        "costs": {"commission_per_share": None, "min_commission": None, "slippage": "0.0005"}
    }
    r = client_db.post("/api/backtests", json=body)
    assert r.status_code == 422
    assert any("佣金" in e for e in r.json()["detail"])
    eng = create_engine(db_url)
    with Session(eng) as s:
        n = s.scalar(select(func.count()).select_from(BacktestRun))
        assert n == 2  # 只有 seed 写入的两条
    eng.dispose()


# ---- build_submission（无数据库）----

FORM: dict[str, Any] = {
    "strategy": "etf_momentum", "universe": ["SPY", "QQQ"], "safe_asset": "SHY",
    "lookback": 126, "top_k": 1, "sma_window": 200, "start": date(2016, 1, 4),
    "end": date(2026, 10, 28), "cash": Decimal("100000"),
    "costs": {"commission_per_share": "0.005", "min_commission": "1", "slippage": "0.0005"},
}  # fmt: skip


def _req(**change: Any) -> BacktestSubmit:
    costs = {**FORM["costs"], **change.pop("costs", {})}
    return BacktestSubmit(**{**FORM, **change, "costs": CostsIn(**costs)})


def test_build_submission_ok() -> None:
    params, errors = build_submission(_req(), CostSettings())
    assert errors == []
    assert params["universe"] == ["SPY", "QQQ"]
    assert params["_costs"] == {
        "commission_per_share": "0.005", "min_commission": "1", "slippage": "0.0005",
    }  # fmt: skip
    assert params["_cash"] == "100000"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"costs": {"commission_per_share": None}}, "佣金"),
        ({"costs": {"min_commission": "-1"}}, "成本参数"),
        ({"top_k": 3}, "策略参数"),
        ({"safe_asset": "SPY"}, "策略参数"),
        ({"universe": []}, "策略参数"),
        ({"start": date(2026, 10, 28)}, "开始日期"),
    ],
)
def test_build_submission_errors(change: dict[str, Any], message: str) -> None:
    _, errors = build_submission(_req(**change), CostSettings())
    assert any(message in e for e in errors), errors
