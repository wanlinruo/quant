"""盘中采集的有界资源记录（#100）。覆盖最多 128 条，不储存报价历史。"""

from __future__ import annotations

import json
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from quant.data.quota import atomic_write_text
from quant.market.session import ET


def record_refresh(
    data_dir: Path,
    kind: str,
    started: float,
    cpu_start: float,
    writes: int,
    requests: int = 0,
    rate_limits: int = 0,
) -> None:
    path = data_dir / "market" / "refresh_metrics.json"
    try:
        rows = json.loads(path.read_text())
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    snap = data_dir / "market" / "snapshot_intraday.json"
    try:
        doc = json.loads(snap.read_text())
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    elapsed = time.monotonic() - started
    today = datetime.now(UTC).astimezone(ET).date().isoformat()
    updated = bool(
        snap.exists()
        and snap.stat().st_mtime >= time.time() - elapsed
        and doc.get("as_of") == today
    )
    rows.append(
        {
            "time": datetime.now(UTC).isoformat(),
            "kind": kind,
            "elapsed_s": round(elapsed, 3),
            "cpu_s": round(time.process_time() - cpu_start, 3),
            "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
            "quote_upserts": writes,
            "requests": requests,
            "rate_limits": rate_limits,
            "snapshot_as_of": doc.get("as_of"),
            "snapshot_updated": updated,
            "coverage": doc.get("coverage") if updated else None,
            "quote_oldest": doc.get("intraday_time_start"),
            "quote_latest": doc.get("intraday_time"),
            "snapshot_bytes": snap.stat().st_size if snap.exists() else 0,
        }
    )
    atomic_write_text(path, json.dumps(rows[-128:], ensure_ascii=False))
