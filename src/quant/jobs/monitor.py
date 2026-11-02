"""monitor：每 60 秒采集主机与容器资源，写入 system_metrics（#71 B 批，设计 5.5、7.2）。

    python -m quant.jobs.monitor [--once] [--interval 60]

- 主机 CPU / 内存读容器内的 /proc（即主机数据），磁盘取数据卷所在文件系统。
- 容器状态经 docker-socket-proxy（只开放 GET 容器查询）读取：QUANT_DOCKER_URL，默认
  http://docker-proxy:2375。只取 compose 项目 quant 的容器。
- inspect 返回的 Env 含密钥：只取 RestartCount 与 StartedAt，不保存、不打印其它字段。
- 采集失败只记日志；Docker 不可用时主机数据照常写入。每小时删除 8 天前的采样。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

import httpx
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from quant.core.settings import ConfigError, load_settings
from quant.db.backtests import make_engine
from quant.db.models import SystemMetric

log = logging.getLogger("quant.monitor")

PROJECT = "quant"
RETENTION = timedelta(days=8)
DEFAULT_DOCKER_URL = "http://docker-proxy:2375"


class DockerApi(Protocol):
    def get(self, path: str) -> Any: ...


class HttpDocker:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=10)

    def get(self, path: str) -> Any:
        r = self.client.get(path)
        r.raise_for_status()
        return r.json()


def parse_cpu(stat: str) -> tuple[int, int]:
    """/proc/stat 第一行 → (忙碌, 总计) jiffies；idle 与 iowait 算空闲。"""
    v = [int(x) for x in stat.splitlines()[0].split()[1:]]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return sum(v) - idle, sum(v)


def cpu_pct(prev: tuple[int, int] | None, cur: tuple[int, int]) -> float | None:
    if prev is None or cur[1] <= prev[1]:
        return None
    return (cur[0] - prev[0]) / (cur[1] - prev[1]) * 100


def parse_mem(meminfo: str) -> tuple[int, int]:
    """(已用, 总计) 字节；已用 = MemTotal − MemAvailable。"""
    kv = {}
    for line in meminfo.splitlines():
        k, _, rest = line.partition(":")
        if rest.strip():
            kv[k] = int(rest.split()[0]) * 1024
    return kv["MemTotal"] - kv["MemAvailable"], kv["MemTotal"]


def _container_stats(st: dict[str, Any]) -> tuple[float | None, int | None, int | None]:
    cpu, pre = st.get("cpu_stats") or {}, st.get("precpu_stats") or {}
    d_cpu = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get(
        "total_usage", 0
    )
    d_sys = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    n = cpu.get("online_cpus") or 1
    pct = d_cpu / d_sys * n * 100 if d_sys > 0 and pre.get("system_cpu_usage") else None
    mem = st.get("memory_stats") or {}
    usage = mem.get("usage")
    used = usage - (mem.get("stats") or {}).get("inactive_file", 0) if usage is not None else None
    return pct, used, mem.get("limit")


class Collector:
    def __init__(self, docker: DockerApi, proc: Path, disk_path: Path) -> None:
        self.docker, self.proc, self.disk_path = docker, proc, disk_path
        self.prev_cpu: tuple[int, int] | None = None

    def _host(self, now: datetime) -> SystemMetric:
        cur = parse_cpu((self.proc / "stat").read_text())
        pct, self.prev_cpu = cpu_pct(self.prev_cpu, cur), cur
        used, total = parse_mem((self.proc / "meminfo").read_text())
        disk = shutil.disk_usage(self.disk_path)
        return SystemMetric(
            ts=now,
            kind="host",
            name="host",
            cpu_pct=pct,
            mem_bytes=used,
            mem_limit=total,
            disk_used=disk.used,
            disk_total=disk.total,
            extra={},
        )

    def _containers(self, now: datetime) -> list[SystemMetric]:
        out = []
        for c in self.docker.get("/containers/json?all=1"):
            labels = c.get("Labels") or {}
            if labels.get("com.docker.compose.project") != PROJECT:
                continue
            name = labels.get("com.docker.compose.service") or c["Names"][0].lstrip("/")
            extra: dict[str, Any] = {"state": c.get("State"), "status": c.get("Status")}
            pct = used = limit = None
            try:
                if c.get("State") == "running":
                    pct, used, limit = _container_stats(
                        self.docker.get(f"/containers/{c['Id']}/stats?stream=false")
                    )
                info = self.docker.get(f"/containers/{c['Id']}/json")
                extra["started_at"] = (info.get("State") or {}).get("StartedAt")
                extra["restarts"] = info.get("RestartCount")
            except (httpx.HTTPError, ValueError) as e:
                log.warning("容器 %s 状态读取失败：%s", name, type(e).__name__)
            out.append(
                SystemMetric(
                    ts=now,
                    kind="container",
                    name=name,
                    cpu_pct=pct,
                    mem_bytes=used,
                    mem_limit=limit,
                    extra=extra,
                )
            )
        return sorted(out, key=lambda m: m.name)

    def collect(self, now: datetime) -> list[SystemMetric]:
        rows = [self._host(now)]
        try:
            rows += self._containers(now)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Docker 代理不可用：%s", type(e).__name__)
        return rows


def save(engine: Engine, rows: list[SystemMetric], now: datetime, prune: bool) -> None:
    with Session(engine) as s, s.begin():
        s.add_all(rows)
        if prune:
            s.execute(delete(SystemMetric).where(SystemMetric.ts < now - RETENTION))


class _Stop:
    requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        self.requested = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="monitor")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=60.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as e:
        log.error("配置错误：%s", e)
        return 1
    if settings.database_url is None:
        log.error("未配置 QUANT_DATABASE_URL")
        return 1
    engine = make_engine(settings.database_url.get_secret_value())
    docker = HttpDocker(os.environ.get("QUANT_DOCKER_URL", DEFAULT_DOCKER_URL))
    collector = Collector(docker, Path("/proc"), settings.data_dir)
    stop = _Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("monitor 启动，每 %.0f 秒采集一次", args.interval)
    last_prune: datetime | None = None
    while not stop.requested:
        now = datetime.now(UTC)
        try:
            rows = collector.collect(now)
            prune = last_prune is None or now - last_prune >= timedelta(hours=1)
            save(engine, rows, now, prune)
            if prune:
                last_prune = now
        except Exception:  # 采集失败不退出，下次再试
            log.exception("采集失败")
        if args.once:
            break
        end = time.monotonic() + args.interval
        while not stop.requested and time.monotonic() < end:
            time.sleep(1)
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
