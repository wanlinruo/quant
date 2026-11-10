"""系统页概览（#71 A 批）：测试库 + 构造的任务记录与数据文件。"""

import json
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


def ov(client: TestClient, mode: str = "paper") -> dict:  # type: ignore[type-arg]
    r = client.get("/api/system/overview", params={"mode": mode})
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


@needs_db
def test_banner_error_with_command(client: TestClient) -> None:
    b = ov(client)["banner"]
    assert b["level"] == "error"
    err = [x for x in b["items"] if x["level"] == "error"]
    assert err and "daily_signal" in err[0]["text"]
    assert err[0]["command"] == "quant job daily_signal --date 2026-10-28 --rerun-failed"
    assert any("熔断开关关闭" in x["text"] for x in b["items"])  # paper 模式下关闭 → 注意


@needs_db
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


@needs_db
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
    assert t["market_data"]["trade_date"] == "2026-10-27"
    assert t["market_data"]["planned_at"].startswith("2026-10-28T00:10")


@needs_db
def test_midnight_market_timeline_and_command_keep_target_day(
    client: TestClient,
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _at(monkeypatch, datetime(2026, 10, 29, 0, 12, tzinfo=ET))
    engine = create_engine(db_url)
    started = datetime(2026, 10, 29, 0, 10, tzinfo=ET)
    with Session(engine) as s, s.begin():
        s.add(JobRun(mode="paper", job_name="market_data", trade_date=SEED_LAST_DAY,
                     status="failed", started_at=started,
                     finished_at=started + timedelta(seconds=1),
                     error="MarketDataPending: HTTP 403"))  # fmt: skip
    engine.dispose()
    d = ov(client)
    t = next(x for x in d["timeline"] if x["job"] == "market_data")
    assert t["trade_date"] == "2026-10-28" and t["status"] == "failed"
    assert t["planned_at"].startswith("2026-10-29T00:10")
    assert any(
        x["command"] == "quant job market_data --date 2026-10-28 --rerun-failed"
        for x in d["banner"]["items"]
    )


@needs_db
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
    assert all(x["date"] <= "2026-11-10" for x in days)  # 10 个交易日内，不含 11-26


@needs_db
def test_sources_pending_without_files(client: TestClient) -> None:
    src = {s["key"]: s for s in ov(client)["sources"]}
    assert src["market"]["status"] == "pending" and src["news"]["status"] == "pending"


@needs_db
def test_stock_source_uses_dates_and_expected_coverage(client: TestClient, tmp_path: Path) -> None:
    from quant.data.market_store import gics_path, market_dir

    gics = gics_path(tmp_path)
    gics.parent.mkdir(parents=True, exist_ok=True)
    gics.write_text(
        "Symbol,Security,GICS Sector,GICS Sub-Industry\n"
        "S0,Co S0,Technology,Hardware\n"
        "S1,Co S1,Technology,Hardware\n",
        encoding="utf-8",
    )
    stocks = market_dir(tmp_path) / "stocks"
    stocks.mkdir(parents=True)
    fresh = NOW.isoformat()
    old = (NOW - timedelta(days=10)).isoformat()
    (stocks / "S0.json").write_text(
        json.dumps({"symbol": "S0", "news_updated": fresh, "metrics_updated": "2026-10-28"})
    )
    (stocks / "S1.json").write_text(
        json.dumps({"symbol": "S1", "news_updated": old, "metrics_updated": "2026-10-28"})
    )

    d = ov(client)
    src = next(s for s in d["sources"] if s["key"] == "stocks")
    assert src["status"] == "warn" and src["progress"] == pytest.approx(0.5)
    assert src["detail"] == "新闻新鲜 1/2 只，财务指标 2/2 只"
    assert any(
        "个股新闻与财务" in x["text"] and "覆盖不足" in x["text"] for x in d["banner"]["items"]
    )


@needs_db
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


def _at(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    import quant.api.system_view as sv

    monkeypatch.setattr(sv, "_now", lambda: now)


def _cells(d: dict, job: str) -> dict:  # type: ignore[type-arg]
    return {
        c["date"]: c["status"] for r in d["grid"]["jobs"] if r["job"] == job for c in r["cells"]
    }


@needs_db
def test_weekend_uses_last_trading_day(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, datetime(2026, 10, 31, 10, 0, tzinfo=ET))  # 周六
    d = ov(client)
    assert d["today"] == "2026-10-30" and d["grid"]["dates"][-1] == "2026-10-30"
    assert "2026-10-31" not in d["grid"]["dates"]
    assert _cells(d, "daily_data")["2026-10-30"] == "missing"  # 周五已过计划时间
    assert not any(c == "pending" for r in d["grid"]["jobs"] for c in [r["cells"][-1]["status"]])


@needs_db
def test_half_day_plan_and_pending(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, datetime(2026, 11, 27, 12, 0, tzinfo=ET))  # 感恩节次日半日市
    d = ov(client)
    t = {x["job"]: x for x in d["timeline"]}
    assert t["daily_data"]["planned_at"].startswith("2026-11-27T13:30")
    assert t["daily_data"]["status"] == "pending"
    assert any("半日市" in tag for x in d["upcoming"] for tag in x["tags"])


@needs_db
def test_grace_after_planned_time(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, datetime(2026, 10, 29, 16, 35, tzinfo=ET))  # 计划 16:30，调度每 30 秒检查
    d = ov(client)
    assert _cells(d, "daily_data")["2026-10-29"] == "pending"
    assert not any("已过计划时间" in x["text"] for x in d["banner"]["items"])


@needs_db
def test_downstream_waits_for_upstream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_url: str
) -> None:
    eng = create_engine(db_url)
    t = datetime(2026, 10, 26, 14, tzinfo=UTC)
    rows = [
        ("daily_execute", 26, "success"),
        ("daily_report", 26, "success"),
        ("daily_signal", 26, "success"),
        ("daily_execute", 29, "running"),
        ("daily_data", 29, "failed"),
    ]
    with Session(eng) as s, s.begin():
        for job, day, st in rows:
            end = None if st == "running" else t
            s.add(
                JobRun(
                    mode="paper",
                    job_name=job,
                    trade_date=date(2026, 10, day),
                    status=st,
                    started_at=t,
                    finished_at=end,
                )
            )
    eng.dispose()
    _at(monkeypatch, datetime(2026, 10, 29, 18, 0, tzinfo=ET))
    d = ov(client)
    assert _cells(d, "daily_report")["2026-10-29"] == "pending"  # 执行还在跑（轮询成交）
    assert _cells(d, "daily_report")["2026-10-27"] == "blocked"  # 当天执行没跑
    assert _cells(d, "daily_signal")["2026-10-29"] == "blocked"  # daily_data 失败，信号不会运行
    texts = [x["text"] for x in d["banner"]["items"]]
    assert not any(x.startswith(("daily_report", "daily_signal")) for x in texts if "未运行" in x)
    assert any("daily_data" in x and x.startswith("daily_data") for x in texts)


@needs_db
def test_timeline_and_risk_follow_mode(client: TestClient) -> None:
    live = ov(client, "live")
    t = {x["job"]: x for x in live["timeline"]}
    assert t["daily_data"]["status"] == "failed" and t["daily_signal"]["status"] == "na"
    risk = {r["label"]: r for r in live["risk"]}
    assert risk["近 30 天被拒订单"]["used"] == 0


@needs_db
def test_overview_includes_resources(client: TestClient, db_url: str) -> None:
    from quant.db.models import SystemMetric

    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add(
            SystemMetric(
                ts=NOW - timedelta(hours=1),
                kind="host",
                name="host",
                cpu_pct=3.0,
                mem_bytes=1,
                mem_limit=2,
                disk_used=1,
                disk_total=10,
                extra={},
            )
        )
    eng.dispose()
    d = ov(client)
    assert d["resources"]["sampled_at"] is not None
    assert any("资源采集已停止" in x["text"] for x in d["banner"]["items"])


# ---- #89 暂缓项 ----


def _cards(d: dict) -> dict:  # type: ignore[type-arg]
    return {c["key"]: c for c in d["cards"]}


@needs_db
def test_reconcile_running_is_not_failure(client: TestClient, db_url: str) -> None:
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add(
            JobRun(
                mode="paper",
                job_name="daily_execute",
                trade_date=SEED_LAST_DAY,
                status="running",
                started_at=NOW - timedelta(hours=11),
            )
        )
    eng.dispose()
    c = _cards(ov(client))["reconcile"]
    assert c["value"] == "执行中" and c["level"] == "info"


@needs_db
def test_today_jobs_counts_only_due_and_live_jobs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at(monkeypatch, datetime(2026, 10, 29, 16, 0, tzinfo=ET))  # 16:30 前
    c = _cards(ov(client))["today_jobs"]
    assert c["value"] == "0 / 0" and c["level"] == "ok"  # 该账户只上线了 data / signal，都未到时间
    _at(monkeypatch, NOW)
    c = _cards(ov(client))["today_jobs"]
    assert c["value"] == "1 / 2" and c["level"] == "warn"


@needs_db
def test_sim_kill_switch_is_info(client: TestClient) -> None:
    assert _cards(ov(client, "sim"))["kill_switch"]["level"] == "info"


@needs_db
def test_weekend_upcoming_starts_next_trading_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at(monkeypatch, datetime(2026, 10, 31, 10, 0, tzinfo=ET))  # 周六
    assert ov(client)["upcoming"][0]["date"] == "2026-11-02"


@needs_db
def test_success_rate_counts_missing_days(client: TestClient, db_url: str) -> None:
    eng = create_engine(db_url)
    t = datetime(2026, 10, 26, 21, tzinfo=UTC)
    with Session(eng) as s, s.begin():  # 10-26 成功、10-27 缺失（daily_data 当天成功）、10-28 失败
        s.add(
            JobRun(
                mode="paper",
                job_name="daily_signal",
                trade_date=date(2026, 10, 26),
                status="success",
                started_at=t,
                finished_at=t,
            )
        )
    eng.dispose()
    sig = {r["job"]: r for r in ov(client)["grid"]["jobs"]}["daily_signal"]
    assert {c["date"]: c["status"] for c in sig["cells"]}["2026-10-27"] == "missing"
    assert sig["success_rate"] == pytest.approx(1 / 3)


@needs_db
def test_overview_when_database_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QUANT_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("TRADING_MODE", "sim")
    _clear()
    r = TestClient(create_app()).get("/api/system/overview", params={"mode": "sim"})
    _clear()
    assert r.status_code == 200
    assert "数据库连接失败" in r.json()["banner"]["items"][0]["text"]
