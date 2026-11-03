"""资源采样有界、损坏后恢复，不保存凭据或追加报价历史。"""

import json
import time
from pathlib import Path

from quant.market.refresh_metrics import record_refresh


def test_resource_log_is_bounded_and_recovers_from_corruption(tmp_path: Path) -> None:
    p = tmp_path / "market" / "refresh_metrics.json"
    p.parent.mkdir()
    p.write_text("corrupt")
    for _ in range(130):
        record_refresh(tmp_path, "quotes", time.monotonic(), time.process_time(), 525)
    rows = json.loads(p.read_text())
    assert len(rows) == 128
    assert rows[-1]["quote_upserts"] == 525 and rows[-1]["snapshot_bytes"] == 0
    assert rows[-1]["peak_rss_bytes"] > 0


def test_old_snapshot_is_not_reported_as_successful_current_round(tmp_path: Path) -> None:
    p = tmp_path / "market" / "snapshot_intraday.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"as_of": "2026-10-30", "coverage": 1.0}))
    record_refresh(tmp_path, "quotes", time.monotonic(), time.process_time(), 525)
    row = json.loads((p.parent / "refresh_metrics.json").read_text())[-1]
    assert not row["snapshot_updated"] and row["coverage"] is None
    assert row["snapshot_as_of"] == "2026-10-30" and row["quote_upserts"] == 525


def test_snapshot_written_this_round_keeps_its_coverage(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from quant.market.session import ET

    started = time.monotonic()
    p = tmp_path / "market" / "snapshot_intraday.json"
    p.parent.mkdir()
    p.write_text(
        json.dumps({"as_of": datetime.now(UTC).astimezone(ET).date().isoformat(), "coverage": 0.99})
    )
    record_refresh(tmp_path, "quotes", started, time.process_time(), 525)
    row = json.loads((p.parent / "refresh_metrics.json").read_text())[-1]
    assert row["snapshot_updated"] and row["coverage"] == 0.99
