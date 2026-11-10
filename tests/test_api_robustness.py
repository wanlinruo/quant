"""回测稳健性接口（#68）：父回测 + 子回测 → 热力图、相邻参数、walk-forward、成本与起点。"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.db.models import BacktestEquity, BacktestRun
from quant.engine.robustness import plan

pytestmark = needs_db
S, E = date(2016, 1, 4), date(2021, 12, 31)


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


def _m(cagr: float, sharpe: float, mdd: float) -> dict[str, Any]:
    return {"cagr": cagr, "sharpe": sharpe, "max_drawdown": mdd, "total_return": 0.1}


def _add_equity(s: Session, run_id: int, drift: float, start: date = S) -> None:
    idx = pd.bdate_range(start, E)
    eq = 100000 * (1 + pd.Series(drift, index=idx)).cumprod()
    s.add_all(
        BacktestEquity(
            run_id=run_id,
            equity_date=d.date(),
            equity=Decimal(f"{v:.2f}"),
            drawdown=0.0,
        )
        for d, v in zip(idx, eq.to_numpy(), strict=True)
    )


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    _clear()
    yield TestClient(create_app())
    _clear()


def _seed(
    db_url: str,
    *,
    strategy: str = "combo",
    params: dict[str, Any] | None = None,
    leave_queued: int = 0,
    fail_one: bool = False,
) -> int:
    eng = create_engine(db_url)
    params = params or (
        {"momentum_weight": 0.4, "swing_weight": 0.6} if strategy == "combo" else {}
    )
    with Session(eng) as s, s.begin():
        parent = BacktestRun(
            strategy=strategy,
            params={**params, "_robust": True},
            start=S,
            end=E,
            status="done",
            metrics=_m(0.2, 1.3, -0.2),
        )
        s.add(parent)
        s.flush()
        _add_equity(s, parent.id, 0.0004)
        variants = plan(strategy, params, S, E)
        for i, v in enumerate(variants):
            status = "done"
            if i < leave_queued:
                status = "queued"
            elif fail_one and v.kind == "extra":
                status, fail_one = "failed", False
            sharpe = 0.9 if (v.x, v.y) == (0, 1) else 1.2
            run = BacktestRun(
                strategy=strategy,
                params=v.params,
                start=v.start,
                end=E,
                status=status,
                metrics=_m(0.1, sharpe, -0.25) if status == "done" else None,
                parent_id=parent.id,
                variant={
                    "kind": v.kind,
                    "label": v.label,
                    "x": v.x,
                    "y": v.y,
                    "neighbor": v.neighbor,
                    "cost_mult": v.cost_mult,
                },
            )
            s.add(run)
            s.flush()
            if status == "done" and v.kind == "grid":
                _add_equity(s, run.id, 0.0002 + 0.0001 * (v.x or 0), v.start)
        pid = parent.id
    eng.dispose()
    return pid


def get(client: TestClient, run_id: int) -> dict[str, Any]:
    r = client.get(f"/api/backtests/{run_id}/robustness")
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def test_not_requested(client: TestClient, db_url: str) -> None:
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        run = BacktestRun(strategy="combo", params={}, start=S, end=E, status="done", metrics={})
        s.add(run)
        s.flush()
        rid = run.id
    eng.dispose()
    d = get(client, rid)
    assert d["status"] == "not_requested" and d["grid"] is None and "新建回测" in d["note"]
    assert client.get("/api/backtests/999999/robustness").status_code == 404


def test_done_suite(client: TestClient, db_url: str) -> None:
    d = get(client, _seed(db_url))
    assert d["status"] == "done" and d["progress"]["total"] == d["progress"]["done"]
    g = d["grid"]
    assert g["x_values"] == ["30%", "40%", "50%"] and len(g["cells"]) == 9
    center = next(c for c in g["cells"] if c["center"])
    assert (center["x"], center["y"]) == (1, 1) and center["metrics"]["sharpe"] == 1.3
    nb = d["neighbors"]
    assert len(nb) == 6
    bad = next(r for r in nb if r["label"].startswith("动量仓比例 30%"))
    assert bad["passed"] is False  # 夏普 0.9 < 1.0
    assert d["neighbors_verdict"] is False
    assert [r["label"] for r in d["costs"]] == ["成本 ×0", "成本 ×1（本次）", "成本 ×2", "成本 ×3"]
    assert d["starts"][0]["base"] is True and len(d["starts"]) == 5
    wf = d["walk_forward"]
    assert wf["segments"] and wf["segments"][0]["year"] == 2019
    assert wf["strategy"]["cagr"] is not None


def test_pending_and_failed(client: TestClient, db_url: str) -> None:
    d = get(client, _seed(db_url, leave_queued=2, fail_one=True))
    assert d["status"] == "pending" and d["progress"]["queued"] == 2
    assert d["walk_forward"] is None or d["walk_forward"]["segments"] == []
    assert d["neighbors_verdict"] is None and "不完整" in d["neighbors_note"]
    assert any(r["status"] == "failed" for r in d["neighbors"])


def test_boundary_neighbors_are_explained_and_not_counted(client: TestClient, db_url: str) -> None:
    pid = _seed(
        db_url,
        strategy="etf_momentum",
        params={"universe": ["SPY", "QQQ"], "top_k": 1},
    )
    d = get(client, pid)
    assert len(d["neighbors"]) == 5
    assert "参数越界未运行（不计入判定）" in d["neighbors_note"]
    assert "动量回看天数 126 · 持有只数 0" in d["neighbors_note"]
    skipped = [c for c in d["grid"]["cells"] if c["status"] == "skipped"]
    assert skipped and all(c["error"] == "参数越界，未运行" for c in skipped)


def test_overview_neighbor_check_uses_children(client: TestClient, db_url: str) -> None:
    pid = _seed(db_url)
    checks = client.get(f"/api/backtests/{pid}/analysis").json()["overview"]["checks"]
    assert checks[-1]["label"].startswith("相邻设置") and checks[-1]["passed"] is False


def test_overview_does_not_load_walk_forward(
    client: TestClient, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """概览只需要相邻判定，不读子回测净值（性能）。"""
    import quant.api.robustness_view as rv

    pid = _seed(db_url)

    def boom(*a: object) -> None:
        raise AssertionError("概览不应计算 walk-forward")

    monkeypatch.setattr(rv, "_walk_forward", boom)
    r = client.get(f"/api/backtests/{pid}/analysis")
    assert r.status_code == 200
    assert r.json()["overview"]["checks"][-1]["passed"] is False


def test_failed_child_shows_reason_and_wf_excludes_it(client: TestClient, db_url: str) -> None:
    pid = _seed(db_url)
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        kid = s.scalars(
            select(BacktestRun).where(BacktestRun.parent_id == pid).order_by(BacktestRun.id)
        ).first()
        assert kid is not None and (kid.variant or {}).get("kind") == "grid"
        kid.status, kid.error, kid.metrics = "failed", "ValueError: 数据不足\n详情", None
        label = (kid.variant or {})["label"]
    eng.dispose()
    d = get(client, pid)
    cell = next(c for c in d["grid"]["cells"] if c["label"] == label)
    assert cell["status"] == "failed" and cell["error"] == "ValueError: 数据不足"
    assert "排除 1 个失败的格子" in d["walk_forward"]["note"]


def test_walk_forward_needs_two_valid_cells(client: TestClient, db_url: str) -> None:
    pid = _seed(db_url)
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        for k in s.scalars(select(BacktestRun).where(BacktestRun.parent_id == pid)):
            if (k.variant or {}).get("kind") == "grid":
                k.status, k.metrics = "failed", None
    eng.dispose()
    wf = get(client, pid)["walk_forward"]
    assert wf["segments"] == [] and "有效格子不足" in wf["note"]


def test_delete_parent_cascades(db_url: str) -> None:
    pid = _seed(db_url)
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.delete(s.get(BacktestRun, pid))
    with Session(eng) as s:
        assert s.scalars(select(BacktestRun).where(BacktestRun.parent_id == pid)).first() is None
        assert s.query(BacktestRun).count() == 0
    eng.dispose()
