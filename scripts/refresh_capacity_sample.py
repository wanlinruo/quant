"""#100：在服务器 compose 目录运行的单次只读资源采样，不读取 .env 或券商配置。

python3 /path/to/refresh_capacity_sample.py >> /tmp/quant-refresh-capacity.jsonl
在交易前、盘中、收盘后重复执行；使用宿主机定时器每 5 分钟调用可获得完整交易日记录。
不要把真实服务器采样原件提交进仓库，issue 只回填聚合值。
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any

SQL = """SELECT json_build_object(
  'db_bytes', pg_database_size(current_database()),
  'wal_lsn', pg_current_wal_lsn()::text,
  'quote_rows', (SELECT count(*) FROM intraday_quotes),
  'quote_table_bytes', pg_total_relation_size('intraday_quotes'),
  'quote_dead_tuples', (SELECT n_dead_tup FROM pg_stat_user_tables
                       WHERE relname='intraday_quotes'),
  'wal_bytes_cluster', (SELECT wal_bytes FROM pg_stat_wal),
  'wal_stats_reset', (SELECT stats_reset FROM pg_stat_wal),
  'latest_quote', (SELECT max(quote_time) FROM intraday_quotes),
  'metrics', (SELECT json_agg(row_to_json(x)) FROM (
      SELECT ts, kind, name, cpu_pct, mem_bytes, disk_used, disk_total
      FROM system_metrics WHERE ts > now() - interval '10 minutes'
      ORDER BY ts DESC LIMIT 120) x)
)"""


def read(args: list[str]) -> Any:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=45, check=False)
    except subprocess.TimeoutExpired:
        return {"unavailable": True, "timeout": True}
    if result.returncode:
        return {"unavailable": True, "exit_code": result.returncode}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return result.stdout.strip()


def main() -> None:
    compose = ["docker", "compose", "-f", "compose.yml", "-f", "compose.server.yml"]
    doc = {
        "time": datetime.now(UTC).isoformat(),
        "database": read(
            [
                *compose,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "quant",
                "-d",
                "quant",
                "-At",
                "-c",
                SQL,
            ]
        ),
        "refresh": read(
            [*compose, "exec", "-T", "quotes", "cat", "/data/parquet/market/refresh_metrics.json"]
        ),
        "disk": read([*compose, "exec", "-T", "quotes", "df", "-B1", "/data/parquet"]),
        "containers": read([*compose, "stats", "--no-stream", "--format", "json"]),
    }
    print(json.dumps(doc, ensure_ascii=False))


if __name__ == "__main__":
    main()
