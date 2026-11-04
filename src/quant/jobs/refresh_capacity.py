"""#100：只读采集容量基线，交给 monitor 随 system_metrics 保留 8 天。

不读取环境配置、券商数据或完整行情；JSON 文件仅保留指定字段。
WAL 与块 I/O 为累计计数，报告须对齐重置点/容器启动时间后计算差值。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from quant.db.models import SystemMetric

log = logging.getLogger("quant.monitor")
INTERVAL = timedelta(minutes=5)
MAX_FILE_BYTES = 1024 * 1024
DATABASE_SQL = """SELECT json_build_object(
  'db_bytes', pg_database_size(current_database()),
  'wal_bytes_cluster', (SELECT wal_bytes FROM pg_stat_wal),
  'wal_stats_reset', (SELECT stats_reset FROM pg_stat_wal),
  'quote_rows', (SELECT count(*) FROM intraday_quotes),
  'quote_table_bytes', pg_total_relation_size('intraday_quotes'),
  'quote_dead_tuples', (SELECT n_dead_tup FROM pg_stat_user_tables
                       WHERE relname = 'intraday_quotes'),
  'latest_quote', (SELECT max(quote_time) FROM intraday_quotes)
)"""
REFRESH_FIELDS = (
    "time",
    "kind",
    "elapsed_s",
    "cpu_s",
    "peak_rss_bytes",
    "quote_upserts",
    "requests",
    "rate_limits",
    "snapshot_as_of",
    "snapshot_updated",
    "coverage",
    "quote_oldest",
    "quote_latest",
    "snapshot_bytes",
)


def _read(path: Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("capacity input too large")
    return json.loads(path.read_text(encoding="utf-8"))


def sample(engine: Engine, data_dir: Path, now: datetime) -> SystemMetric:
    extra: dict[str, Any] = {}
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '3s'"))
            extra["database"] = conn.scalar(text(DATABASE_SQL))
    except Exception as e:
        extra["database"] = {"unavailable": type(e).__name__}
        log.warning("容量数据库采样失败：%s", type(e).__name__)
    market = data_dir / "market"
    for name in ("snapshot", "snapshot_intraday", "news", "calendar"):
        try:
            doc = _read(market / f"{name}.json")
            extra[name] = {k: doc.get(k) for k in ("as_of", "generated_at", "stale", "intraday")}
        except (OSError, ValueError, AttributeError) as e:
            extra[name] = {"unavailable": type(e).__name__}
    try:
        records = _read(market / "refresh_metrics.json")
        latest = {}
        for row in records if isinstance(records, list) else []:
            if isinstance(row, dict) and row.get("kind") in ("quotes", "extras"):
                latest[row["kind"]] = {k: row.get(k) for k in REFRESH_FIELDS}
        extra["refresh"] = latest
    except (OSError, ValueError) as e:
        extra["refresh"] = {"unavailable": type(e).__name__}
    return SystemMetric(ts=now, kind="capacity", name="refresh", extra=extra)
