"""系统页资源部分（#71 B 批，设计 5.5）：读 monitor 写入的 system_metrics，只读。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant.api.schemas import (
    SysContainerOut,
    SysDiskOut,
    SysItemOut,
    SysResourceOut,
    SysResourcesOut,
    SysTrendOut,
)
from quant.db.models import SystemMetric
from quant.engine.runner import code_version

ET = ZoneInfo("America/New_York")
STALE = timedelta(minutes=5)
DAY = timedelta(days=1)
GB = 1024**3
# (注意, 出错) 阈值，百分比
LIMITS = {"cpu": (80.0, 95.0), "mem": (85.0, 95.0), "disk": (85.0, 95.0)}
FULL_WARN_DAYS = 30


def _pct(a: int | None, b: int | None) -> float | None:
    return a / b * 100 if a is not None and b else None


def _value(m: SystemMetric, key: str) -> float | None:
    if key == "cpu":
        return m.cpu_pct
    if key == "mem":
        return _pct(m.mem_bytes, m.mem_limit)
    return _pct(m.disk_used, m.disk_total)


def _aggregate_host_metrics(
    s: Session,
    start: datetime,
    end: datetime,
    step: timedelta,
    n: int,
) -> tuple[dict[str, list[float | None]], list[tuple[datetime, float]]]:
    """在数据库内聚合资源采样，避免把分钟级原始行全部载入 API 进程。"""
    seconds = step.total_seconds()
    bucket = func.floor(func.extract("epoch", SystemMetric.ts - start) / seconds).label("bucket")
    mem_pct = 100.0 * SystemMetric.mem_bytes / func.nullif(SystemMetric.mem_limit, 0)
    disk_pct = 100.0 * SystemMetric.disk_used / func.nullif(SystemMetric.disk_total, 0)
    rows = s.scalars(
        select(
            func.jsonb_build_array(
                bucket,
                func.avg(SystemMetric.cpu_pct),
                func.avg(mem_pct),
                func.avg(disk_pct),
                func.avg(SystemMetric.disk_used),
            )
        )
        .where(
            SystemMetric.kind == "host",
            SystemMetric.name == "host",
            SystemMetric.ts >= start,
            SystemMetric.ts <= end,
        )
        .group_by(bucket)
        .order_by(bucket)
    )
    series: dict[str, list[float | None]] = {
        "cpu": [None] * n,
        "mem": [None] * n,
        "disk": [None] * n,
    }
    disk_points: list[tuple[datetime, float]] = []
    for bucket_no, cpu, mem, disk, disk_used in rows:
        i = int(bucket_no)
        if not 0 <= i < n:
            continue
        for key, value in (("cpu", cpu), ("mem", mem), ("disk", disk)):
            series[key][i] = float(value) if value is not None else None
        if disk_used is not None:
            disk_points.append((start + i * step, float(disk_used)))
    return series, disk_points


def _level(key: str, v: float | None) -> Literal["ok", "warn", "error", "info"]:
    if v is None or key not in LIMITS:
        return "info"
    warn, err = LIMITS[key]
    return "error" if v >= err else "warn" if v >= warn else "ok"


def _forecast(pts: Sequence[tuple[datetime, float]], latest: SystemMetric | None) -> SysDiskOut:
    if not pts:
        return SysDiskOut(note="暂无采样")
    out = SysDiskOut(
        used=latest.disk_used if latest else None,
        total=latest.disk_total if latest else None,
    )
    if pts[-1][0] - pts[0][0] < DAY:
        out.note = "采样不足 24 小时，暂不预测"
        return out
    t0 = pts[0][0]
    xs = [(t - t0) / DAY for t, _ in pts]
    ys = [float(u) for _, u in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den if den else 0.0
    out.per_day = slope
    if (
        slope < 1024**2 or latest is None or latest.disk_total is None or latest.disk_used is None
    ):  # 每天不足 1 MB 视为未增长
        out.note = "近 7 天用量未增长，不会写满"
        return out
    out.days_left = (latest.disk_total - latest.disk_used) / slope
    out.note = f"每天约增长 {slope / GB:.2f} GB"
    return out


def resources(s: Session, now: datetime) -> SysResourcesOut:
    week = now - timedelta(days=7)
    latest = s.scalars(
        select(SystemMetric)
        .where(
            SystemMetric.kind == "host",
            SystemMetric.name == "host",
            SystemMetric.ts >= week,
            SystemMetric.ts <= now,
        )
        .order_by(SystemMetric.ts.desc())
        .limit(1)
    ).first()
    out = SysResourcesOut(version=code_version())
    day_series, _ = _aggregate_host_metrics(s, now - DAY, now, timedelta(minutes=5), 288)
    labels = {"cpu": "CPU", "mem": "内存", "disk": "磁盘"}
    for key, label in labels.items():
        v = _value(latest, key) if latest else None
        text = ""
        if latest and key == "mem" and latest.mem_bytes is not None and latest.mem_limit:
            text = f"{latest.mem_bytes / GB:.1f} / {latest.mem_limit / GB:.1f} GB"
        if latest and key == "disk" and latest.disk_used is not None and latest.disk_total:
            text = f"{latest.disk_used / GB:.1f} / {latest.disk_total / GB:.1f} GB"
        out.cards.append(
            SysResourceOut(
                key=key,
                label=label,
                value=v,
                text=text or ("暂无采样" if latest is None else ""),
                level=_level(key, v),
                series=day_series[key],
            )
        )
    out.cards.append(
        SysResourceOut(key="latency", label="老虎接口延迟", text="待接入（monitor 不持有券商凭证）")
    )
    hour0 = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=7 * 24 - 1)
    hrs = [hour0 + timedelta(hours=i) for i in range(7 * 24)]
    hour_series, disk_points = _aggregate_host_metrics(s, hour0, now, timedelta(hours=1), len(hrs))
    out.trends = SysTrendOut(
        times=hrs,
        **hour_series,
    )
    out.disk = _forecast(disk_points, latest)

    last_c = s.scalar(select(func.max(SystemMetric.ts)).where(SystemMetric.kind == "container"))
    if last_c is not None:
        for m in s.scalars(
            select(SystemMetric)
            .where(SystemMetric.kind == "container", SystemMetric.ts == last_c)
            .order_by(SystemMetric.name)
        ):
            e = m.extra or {}
            out.containers.append(
                SysContainerOut(
                    name=m.name,
                    state=e.get("state"),
                    status=e.get("status"),
                    cpu_pct=m.cpu_pct,
                    mem_bytes=m.mem_bytes,
                    mem_limit=m.mem_limit,
                    started_at=e.get("started_at"),
                    restarts=e.get("restarts"),
                    stale=now - last_c > STALE,
                )
            )

    # 不限时间窗：monitor 停了超过 7 天，表里仍有旧采样（只有 monitor 自己删旧数据）
    last_h = s.scalar(
        select(func.max(SystemMetric.ts)).where(
            SystemMetric.kind == "host", SystemMetric.name == "host"
        )
    )
    if last_h is None:
        return out
    out.sampled_at = last_h
    warn = out.items.append
    if now - last_h > STALE:
        et = last_h.astimezone(ET)
        warn(
            SysItemOut(
                level="warn",
                text=f"资源采集已停止：最近采样 {et:%m-%d %H:%M} ET（检查 monitor 容器）",
            )
        )
    if latest is None:
        return out
    disk_pct = _value(latest, "disk")
    if disk_pct is not None and disk_pct >= LIMITS["disk"][0]:
        warn(SysItemOut(level="warn", text=f"磁盘已用 {disk_pct:.0f}%"))
    if out.disk.days_left is not None and out.disk.days_left < FULL_WARN_DAYS:
        warn(SysItemOut(level="warn", text=f"磁盘预计 {out.disk.days_left:.0f} 天后写满"))
    if out.containers and out.containers[0].stale and now - last_h <= STALE:
        warn(
            SysItemOut(level="warn", text="容器状态读取中断：主机采样正常，检查 docker-proxy 容器")
        )
    for c in out.containers:
        if not c.stale and c.state != "running":
            warn(SysItemOut(level="warn", text=f"容器 {c.name} 未在运行：{c.state}"))
    return out
