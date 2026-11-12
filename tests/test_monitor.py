"""资源采集（#71 B 批）：主机 /proc、磁盘、容器状态 → system_metrics。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.db.models import SystemMetric
from quant.jobs import monitor
from quant.jobs.refresh_capacity import sample

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


def test_next_deadline_keeps_fixed_cadence_and_skips_missed_slots() -> None:
    assert monitor.next_deadline(100.0, 110.0, 60.0) == 160.0
    assert monitor.next_deadline(100.0, 160.0, 60.0) == 160.0
    assert monitor.next_deadline(100.0, 170.0, 60.0) == 220.0
    assert monitor.next_deadline(100.0, 281.0, 60.0) == 340.0
    with pytest.raises(ValueError, match="positive"):
        monitor.next_deadline(100.0, 110.0, 0.0)


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
                raise OSError("down")
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
    assert host.cpu_pct is None and host.mem_bytes == 3000000 * 1024 and (host.disk_total or 0) > 0
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
            raise OSError("proxy down")

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


def test_skips_oneoff_containers(tmp_path: Path) -> None:
    class OneOff(FakeDocker):
        def get(self, path: str) -> Any:
            if path.startswith("/containers/json"):
                base = super().get(path)
                run = dict(base[0], Id="c3", State="exited", Names=["/quant-scheduler-run-1"])
                run["Labels"] = dict(base[0]["Labels"], **{"com.docker.compose.oneoff": "True"})
                return [*base, run]
            return super().get(path)

    rows = monitor.Collector(OneOff(), _proc(tmp_path, STAT1), tmp_path).collect(NOW)
    assert [r.name for r in rows if r.kind == "container"] == ["scheduler"]


def test_malformed_docker_payload_keeps_host(tmp_path: Path) -> None:
    class Weird(FakeDocker):
        def get(self, path: str) -> Any:
            if path.endswith("/json") and path != "/containers/json?all=1":
                return ["not", "a", "dict"]
            return super().get(path)

    rows = monitor.Collector(Weird(), _proc(tmp_path, STAT1), tmp_path).collect(NOW)
    assert any(r.kind == "host" for r in rows)


def test_monitor_uses_only_runtime_dependencies() -> None:
    """生产镜像不装开发依赖（httpx 等），monitor 只能用标准库与运行时依赖。"""
    src = Path(monitor.__file__).read_text(encoding="utf-8")
    assert "httpx" not in src


def test_http_docker_reads_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    seen = []

    def fake_urlopen(url: str, timeout: float) -> Any:
        seen.append(url)
        return io.BytesIO(b'[{"Id": "x"}]')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert monitor.HttpDocker("http://docker-proxy:2375/").get("/containers/json?all=1") == [
        {"Id": "x"}
    ]
    assert seen == ["http://docker-proxy:2375/containers/json?all=1"]


def test_capacity_sample_partial_inputs_bounded_and_redacted(tmp_path: Path) -> None:
    market = tmp_path / "market"
    market.mkdir()
    (market / "snapshot.json").write_text(
        json.dumps(
            {
                "as_of": "2026-11-02",
                "stale": True,
                "secret": "must-not-store",
            }
        )
    )
    (market / "news.json").write_text("{broken")
    (market / "calendar.json").write_text("x" * (1024 * 1024 + 1))
    (market / "refresh_metrics.json").write_text(
        json.dumps(
            [
                {"kind": "quotes", "time": "older", "requests": 100},
                {"kind": "quotes", "time": "newest", "requests": 525, "secret": "must-not-store"},
                {"kind": "extras", "requests": 44},
                {"kind": "unknown", "secret": "must-not-store"},
            ]
        )
    )
    eng = create_engine("sqlite://")  # PostgreSQL 不可用也必须保留文件摘要与其他监控
    row = sample(eng, tmp_path, NOW)
    eng.dispose()
    assert row.kind == "capacity" and row.ts == NOW
    assert row.extra["database"]["unavailable"] == "OperationalError"
    assert row.extra["snapshot"]["stale"] is True
    assert row.extra["news"]["unavailable"] == "JSONDecodeError"
    assert row.extra["calendar"]["unavailable"] == "ValueError"
    assert row.extra["snapshot_intraday"]["unavailable"] == "FileNotFoundError"
    assert row.extra["refresh"]["quotes"]["time"] == "newest"
    assert row.extra["refresh"]["extras"]["requests"] == 44
    assert "must-not-store" not in json.dumps(row.extra)


@needs_db
def test_capacity_sample_database_and_retention(db_url: str, tmp_path: Path) -> None:
    eng = create_engine(db_url)
    row = sample(eng, tmp_path, NOW)
    assert row.extra["database"]["db_bytes"] > 0
    assert "wal_stats_reset" in row.extra["database"]
    assert "quote_dead_tuples" in row.extra["database"]
    with Session(eng) as s, s.begin():
        s.add(SystemMetric(ts=NOW - timedelta(days=9), kind="capacity", name="refresh", extra={}))
    monitor.save(eng, [row], NOW, prune=True)
    with Session(eng) as s:
        capacity = s.scalars(select(SystemMetric).where(SystemMetric.kind == "capacity")).all()
        assert [r.ts for r in capacity] == [NOW]
        assert capacity[0].extra["database"]["db_bytes"] > 0
    eng.dispose()


def test_monitor_records_block_io_without_dumping_docker_payload(tmp_path: Path) -> None:
    class IoDocker(FakeDocker):
        def get(self, path: str) -> Any:
            doc = super().get(path)
            if path.endswith("/stats?stream=false"):
                doc["blkio_stats"] = {
                    "io_service_bytes_recursive": [
                        {"op": "read", "value": 10},  # Docker Engine 29 uses lowercase
                        {"op": "Read", "value": 20},
                        {"op": "write", "value": 40},
                        {"op": "Total", "value": 70},
                    ]
                }
                doc["secret"] = "must-not-store"
            return doc

    rows = monitor.Collector(IoDocker(), _proc(tmp_path, STAT1), tmp_path).collect(NOW)
    extra = next(r.extra for r in rows if r.kind == "container")
    assert extra["io_read_bytes"] == 30 and extra["io_write_bytes"] == 40
    assert "must-not-store" not in json.dumps(extra)
