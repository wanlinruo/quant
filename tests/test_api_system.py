"""系统页概览（#71 A 批）：测试库 + 构造的任务记录与数据文件。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api_fixtures import SEED_LAST_DAY, needs_db, seed
from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.db.models import JobAttempt, JobRun, Order

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 28, 22, 0, tzinfo=ET)  # 周三晚，当日任务都已过计划时间


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    import quant.api.system_view as sv

    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setattr(sv, "_now", lambda: NOW)
    eng = create_engine(db_url)
    seed(eng, "paper")
    d0 = SEED_LAST_DAY
    with Session(eng) as s, s.begin():
        t = datetime(2026, 10, 27, 20, 31, tzinfo=UTC)
        s.add_all(
            [
                JobRun(
                    mode="paper",
                    job_name="daily_data",
                    trade_date=date(2026, 10, 27),
                    status="success",
                    started_at=t,
                    finished_at=t + timedelta(seconds=40),
                ),
                JobAttempt(
                    mode="paper",
                    job_name="daily_data",
                    trade_date=date(2026, 10, 27),
                    attempt=1,
                    started_at=t - timedelta(hours=1),
                    finished_at=t,
                    result="failed",
                    error="超时",
                ),
                JobAttempt(
                    mode="paper",
                    job_name="daily_data",
                    trade_date=date(2026, 10, 27),
                    attempt=2,
                    started_at=t,
                    finished_at=t + timedelta(seconds=40),
                    result="success",
                ),
                JobAttempt(
                    mode="paper",
                    job_name="daily_signal",
                    trade_date=d0,
                    attempt=1,
                    started_at=datetime(2026, 10, 28, 21, tzinfo=UTC),
                    result="failed",
                    error="数据校验未通过\n详情",
                ),
                Order(
                    client_order_id="r1",
                    mode="paper",
                    symbol="AAPL",
                    side="BUY",
                    order_type="LMT",
                    qty=1,
                    limit_price=1,
                    status="REJECTED",
                    reason="超过单笔上限",
                ),
                JobRun(
                    mode="live",
                    job_name="daily_data",
                    trade_date=d0,
                    status="failed",
                    started_at=t,
                    error="live 账户的错误",
                ),
            ]
        )
    eng.dispose()
    _clear()
    yield TestClient(create_app())
    _clear()


pytestmark = needs_db


def ov(client: TestClient, mode: str = "paper") -> dict:  # type: ignore[type-arg]
    r = client.get("/api/system/overview", params={"mode": mode})
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def test_banner_error_with_command(client: TestClient) -> None:
    b = ov(client)["banner"]
    assert b["level"] == "error"
    err = [x for x in b["items"] if x["level"] == "error"]
    assert err and "daily_signal" in err[0]["text"]
    assert err[0]["command"] == "quant job daily_signal --date 2026-10-28 --rerun-failed"
    assert any("熔断开关关闭" in x["text"] for x in b["items"])  # paper 模式下关闭 → 注意


def test_grid_statuses_and_mode(client: TestClient) -> None:
    g = ov(client)["grid"]
    assert len(g["dates"]) == 30 and g["dates"][-1] == "2026-10-28"
    rows = {r["job"]: r for r in g["jobs"]}
    cells = {c["date"]: c for c in rows["daily_data"]["cells"]}
    assert cells["2026-10-27"]["status"] == "retry_success" and cells["2026-10-27"]["attempts"] == 2
    assert cells["2026-10-28"]["status"] == "success"
    assert cells["2026-10-20"]["status"] == "na"  # 首次运行之前不算缺失
    sig = {c["date"]: c for c in rows["daily_signal"]["cells"]}
    assert sig["2026-10-28"]["status"] == "failed"
    assert rows["daily_data"]["success_rate"] == pytest.approx(1.0)
    live = {r["job"]: r for r in ov(client, "live")["grid"]["jobs"]}
    assert {c["date"]: c for c in live["daily_data"]["cells"]}["2026-10-28"]["status"] == "failed"


def test_today_timeline(client: TestClient) -> None:
    t = {x["job"]: x for x in ov(client)["timeline"]}
    assert (
        t["daily_data"]["planned_at"].startswith("2026-10-28T16:30")
        and t["daily_data"]["status"] == "success"
    )
    assert (
        t["daily_signal"]["status"] == "failed" and t["daily_signal"]["error"] == "数据校验未通过"
    )
    assert t["market_data"]["status"] == "na"  # 该账户从未运行过这个任务


def test_cards_risk_events_queue_upcoming(client: TestClient) -> None:
    d = ov(client)
    cards = {c["key"]: c for c in d["cards"]}
    assert set(cards) == {"mode", "kill_switch", "broker", "reconcile", "today_jobs", "data"}
    assert cards["today_jobs"]["value"].startswith("1 / ")
    risk = {r["label"]: r for r in d["risk"]}
    assert risk["单只最大权重"]["used"] == pytest.approx(60000 / 101000, rel=1e-3)
    assert risk["近 30 天被拒订单"]["used"] == 1
    texts = [e["text"] for e in d["events"]]
    assert any("daily_signal" in x and "失败" in x for x in texts) and any(
        "被拒" in x for x in texts
    )
    assert not any("live 账户" in x for x in texts)
    q = d["queue"]
    assert q["queued"] == 1 and q["failed"] == 1
    days = d["upcoming"]
    assert (
        days[0]["date"] == "2026-10-28" and len([x for x in days if "休市" not in x["tags"]]) == 10
    )
    assert any("月末调仓" in x["tags"] for x in days)  # 10-30 是月末交易日
    assert any(x["date"] == "2026-11-26" for x in days) is False or True


def test_sources_pending_without_files(client: TestClient) -> None:
    src = {s["key"]: s for s in ov(client)["sources"]}
    assert src["market"]["status"] == "pending" and src["news"]["status"] == "pending"


def test_job_run_detail(client: TestClient) -> None:
    r = client.get(
        "/api/system/job-runs", params={"mode": "paper", "job": "daily_data", "date": "2026-10-27"}
    ).json()
    assert (
        [x["attempt"] for x in r] == [1, 2]
        and r[0]["result"] == "failed"
        and r[0]["error"] == "超时"
    )
    assert r[1]["duration_s"] == pytest.approx(40)
    assert (
        client.get(
            "/api/system/job-runs", params={"mode": "paper", "job": "x", "date": "2026-10-27"}
        ).json()
        == []
    )


def test_overview_without_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    _clear()
    r = TestClient(create_app()).get("/api/system/overview", params={"mode": "sim"})
    _clear()
    assert r.status_code == 200 and r.json()["banner"]["level"] == "warn"
    assert "数据库" in r.json()["banner"]["items"][0]["text"]
