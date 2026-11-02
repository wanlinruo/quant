"""资源采集（#71 B 批）：主机 /proc、磁盘、容器状态 → system_metrics。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.db.models import SystemMetric
from quant.jobs import monitor

STAT1 = "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\n"
STAT2 = "cpu  150 0 150 900 0 0 0 0 0 0\n"
MEMINFO = "MemTotal:       4000000 kB\nMemFree:  100 kB\nMemAvailable:   1000000 kB\n"
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def test_host_cpu_needs_previous_sample() -> None:
    a = monitor.parse_cpu(STAT1)
    b = monitor.parse_cpu(STAT2)
    assert monitor.cpu_pct(None, a) is None  # 第一次采样不记 0
    assert monitor.cpu_pct(a, b) == pytest.approx(50.0)  # busy +100 / total +200


def test_mem() -> None:
    assert monitor.parse_mem(MEMINFO) == (3000000 * 1024, 4000000 * 1024)


class FakeDocker:
    def __init__(self, fail_stats: bool = False) -> None:
        self.fail_stats = fail_stats

    def get(self, path: str) -> Any:
        if path.startswith("/containers/json"):
            return [
                {
                    "Id": "a1",
                    "Names": ["/quant-scheduler-1"],
                    "State": "running",
                    "Status": "Up 3 hours",
                    "Labels": {
                        "com.docker.compose.project": "quant",
                        "com.docker.compose.service": "scheduler",
                    },
                },
                {"Id": "b2", "Names": ["/other"], "State": "running", "Status": "Up", "Labels": {}},
            ]
        if path.endswith("/stats?stream=false"):
            if self.fail_stats:
                raise httpx.ConnectError("down")
            return {
                "cpu_stats": {
                    "cpu_usage": {"total_usage": 3_000},
                    "system_cpu_usage": 20_000,
                    "online_cpus": 2,
                },
                "precpu_stats": {"cpu_usage": {"total_usage": 1_000}, "system_cpu_usage": 10_000},
                "memory_stats": {"usage": 500, "limit": 4000, "stats": {"inactive_file": 100}},
            }
        if path == "/containers/a1/json":
            return {
                "RestartCount": 2,
                "State": {"StartedAt": "2026-11-02T12:00:00Z"},
                "Config": {"Env": ["QUANT_FINNHUB_API_KEY=secret-key"]},
            }
        raise AssertionError(path)


def _proc(tmp_path: Path, stat: str) -> Path:
    (tmp_path / "stat").write_text(stat)
    (tmp_path / "meminfo").write_text(MEMINFO)
    return tmp_path


def test_collect_host_and_containers(tmp_path: Path) -> None:
    c = monitor.Collector(FakeDocker(), _proc(tmp_path, STAT1), tmp_path)
    rows = c.collect(NOW)
    host = next(r for r in rows if r.kind == "host")
    assert host.cpu_pct is None and host.mem_bytes == 3000000 * 1024 and host.disk_total > 0
    (tmp_path / "stat").write_text(STAT2)
    rows = c.collect(NOW)
    host = next(r for r in rows if r.kind == "host")
    assert host.cpu_pct == pytest.approx(50.0)
    cont = [r for r in rows if r.kind == "container"]
    assert [r.name for r in cont] == ["scheduler"]  # 只取 compose 项目 quant
    r = cont[0]
    assert r.cpu_pct == pytest.approx(40.0)  # 2000/10000 × 2 核 × 100
    assert r.mem_bytes == 400 and r.mem_limit == 4000
    assert r.extra == {
        "state": "running",
        "status": "Up 3 hours",
        "started_at": "2026-11-02T12:00:00Z",
        "restarts": 2,
    }
    assert "secret" not in repr(rows)


def test_docker_failure_keeps_host(tmp_path: Path) -> None:
    c = monitor.Collector(FakeDocker(fail_stats=True), _proc(tmp_path, STAT1), tmp_path)
    rows = c.collect(NOW)
    assert any(r.kind == "host" for r in rows)
    cont = [r for r in rows if r.kind == "container"]
    assert cont and cont[0].cpu_pct is None and cont[0].extra["state"] == "running"


def test_docker_unreachable_keeps_host(tmp_path: Path) -> None:
    class Down:
        def get(self, path: str) -> Any:
            raise httpx.ConnectError("proxy down")

    rows = monitor.Collector(Down(), _proc(tmp_path, STAT1), tmp_path).collect(NOW)
    assert [r.kind for r in rows] == ["host"]


@needs_db
def test_save_and_prune(db_url: str) -> None:
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.query(SystemMetric).delete()
        s.add(SystemMetric(ts=NOW - timedelta(days=9), kind="host", name="host", extra={}))
    monitor.save(
        eng,
        [SystemMetric(ts=NOW, kind="host", name="host", cpu_pct=1.0, extra={})],
        NOW,
        prune=True,
    )
    with Session(eng) as s:
        assert [m.ts for m in s.scalars(select(SystemMetric))] == [NOW]
        s.query(SystemMetric).delete()
        s.commit()
    eng.dispose()
