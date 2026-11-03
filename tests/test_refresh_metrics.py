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
