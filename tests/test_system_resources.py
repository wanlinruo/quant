"""系统页资源（#71 B 批）：system_metrics → 资源卡、服务容器、7 天趋势、磁盘预测。"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.api import system_resources as sr
from quant.db.models import SystemMetric

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
GB = 1024**3
pytestmark = needs_db


@pytest.fixture
def s(db_url: str) -> Iterator[Session]:
    eng = create_engine(db_url)
    with Session(eng) as ses:
        ses.query(SystemMetric).delete()
        ses.commit()
        yield ses
        ses.rollback()
        ses.query(SystemMetric).delete()
        ses.commit()
    eng.dispose()


def host(ts: datetime, cpu: float | None, disk_gb: float, mem_gb: float = 2.0) -> SystemMetric:
    return SystemMetric(
        ts=ts,
        kind="host",
        name="host",
        cpu_pct=cpu,
        mem_bytes=int(mem_gb * GB),
        mem_limit=4 * GB,
        disk_used=int(disk_gb * GB),
        disk_total=100 * GB,
        extra={},
    )


def cont(ts: datetime, name: str, state: str = "running", restarts: int = 0) -> SystemMetric:
    return SystemMetric(
        ts=ts,
        kind="container",
        name=name,
        cpu_pct=1.5,
        mem_bytes=GB // 2,
        mem_limit=GB,
        extra={
            "state": state,
            "status": "Up 2 hours",
            "started_at": "2026-11-02T13:00:00Z",
            "restarts": restarts,
        },
    )


def test_no_samples(s: Session) -> None:
    r = sr.resources(s, NOW)
    assert r.sampled_at is None and r.items == [] and r.containers == []
    assert {c.key for c in r.cards} == {"cpu", "mem", "disk", "latency"}
    assert all(c.value is None for c in r.cards)
    assert r.disk.days_left is None and "暂无采样" in r.disk.note


def test_cards_series_containers_and_forecast(s: Session) -> None:
    # 3 天，每 10 分钟一次；磁盘每天增长 2 GB
    n = 3 * 24 * 6
    for i in range(n + 1):
        ts = NOW - timedelta(minutes=10 * (n - i))
        s.add(host(ts, 20.0 if i else None, 50 + 2 * i / (24 * 6)))
    s.add_all(
        [cont(NOW, "scheduler"), cont(NOW, "dashboard", restarts=3), cont(NOW, "monitor", "exited")]
    )
    s.commit()
    r = sr.resources(s, NOW)
    cards = {c.key: c for c in r.cards}
    assert cards["cpu"].value == pytest.approx(20.0)
    assert cards["mem"].value == pytest.approx(50.0) and "2.0 / 4.0 GB" in cards["mem"].text
    assert cards["disk"].value == pytest.approx(56.0)
    assert len(cards["cpu"].series) == 288  # 24 小时、5 分钟一格
    assert cards["latency"].value is None and "待接入" in cards["latency"].text
    names = [c.name for c in r.containers]
    assert names == ["dashboard", "monitor", "scheduler"]
    assert r.containers[0].restarts == 3 and not r.containers[0].stale
    assert len(r.trends.times) == 7 * 24 and r.trends.disk[-1] == pytest.approx(56.0, abs=0.2)
    assert r.disk.per_day == pytest.approx(2 * GB, rel=0.01)
    assert r.disk.days_left == pytest.approx(44 / 2, rel=0.02)
    texts = [i.text for i in r.items]
    assert any("monitor" in t and "exited" in t for t in texts)


def test_disk_not_growing_and_short_span(s: Session) -> None:
    s.add_all([host(NOW - timedelta(hours=h), 5.0, 60) for h in range(30)])
    s.commit()
    assert sr.resources(s, NOW).disk.days_left is None
    assert "未增长" in sr.resources(s, NOW).disk.note
    s.query(SystemMetric).delete()
    s.add_all([host(NOW - timedelta(hours=h), 5.0, 60 - h) for h in range(5)])
    s.commit()
    r = sr.resources(s, NOW)
    assert r.disk.days_left is None and "24 小时" in r.disk.note


def test_warnings_stale_disk_full(s: Session) -> None:
    old = NOW - timedelta(minutes=30)
    s.add_all([host(old - timedelta(hours=h), 5.0, 90 + 0.1 * (48 - h)) for h in range(48, -1, -1)])
    s.add(cont(old, "scheduler"))
    s.commit()
    r = sr.resources(s, NOW)
    texts = " ".join(i.text for i in r.items)
    assert "资源采集已停止" in texts and "磁盘" in texts
    assert all(c.stale for c in r.containers)
    assert {c.key: c for c in r.cards}["disk"].level in ("warn", "error")


def test_monitor_stopped_longer_than_window(s: Session) -> None:
    s.add(host(NOW - timedelta(days=10), 5.0, 50))
    s.commit()
    r = sr.resources(s, NOW)
    assert any("资源采集已停止" in i.text for i in r.items)
