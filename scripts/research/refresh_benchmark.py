"""#100：525 只、260 个交易日的离线快照基准，不联网、不连接数据库。

uv run python scripts/research/refresh_benchmark.py --rounds 5
生成的日线在临时目录，退出后清理。只测本机计算与文件读写，不代表线上容器或网络延迟。
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant.core.calendar import get_calendar
from quant.data.market_store import ETF_SET, SECTOR_ETF, CapInfo, GicsTable, MarketStore
from quant.data.providers.finnhub import Quote
from quant.jobs.quotes import _write_snapshot


def benchmark(root: Path, rounds: int) -> dict[str, object]:
    cal = get_calendar()
    day = date(2026, 11, 2)
    now = datetime(2026, 11, 2, 17, tzinfo=UTC)
    members = [f"S{i:03d}" for i in range(503)]
    symbols = [*ETF_SET, *members]
    sectors = list(SECTOR_ETF)
    gics = GicsTable(
        {s: (f"Synthetic {s}", sectors[i % len(sectors)]) for i, s in enumerate(members)}
    )
    store = MarketStore(root)
    days = cal.trading_days(day - timedelta(days=450), cal.previous_trading_day(day))[-260:]
    for i, d in enumerate(days):
        values = 100 + np.arange(len(symbols)) * 0.1 + i * 0.01
        frame = pd.DataFrame(
            {
                "symbol": symbols,
                "open": values - 0.1,
                "high": values + 0.2,
                "low": values - 0.2,
                "close": values,
                "volume": np.full(len(symbols), 1_000_000.0),
                "vwap": values,
            }
        )
        store.write_day(d, frame, symbols)
    store.write_caps({s: CapInfo(100e9 + i * 1e9, s, day) for i, s in enumerate(members)})
    quotes = {
        s: Quote(105 + i * 0.1, 100 + i * 0.1, 0.01, now - timedelta(seconds=i), 100, 200, 99)
        for i, s in enumerate(symbols)
    }
    elapsed, cpu = [], []
    for _ in range(rounds):
        start, c = time.perf_counter(), time.process_time()
        _write_snapshot(root, gics, set(members[:4]), quotes.copy(), now, symbols)
        elapsed.append(round(time.perf_counter() - start, 4))
        cpu.append(round(time.process_time() - c, 4))
    path = root / "market" / "snapshot_intraday.json"
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "environment": platform.platform(),
        "kind": "synthetic-local-only",
        "symbols": len(symbols),
        "history_days": len(days),
        "rounds": rounds,
        "elapsed_s": elapsed,
        "cpu_s": cpu,
        "process_peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "snapshot_bytes": path.stat().st_size,
        "coverage": json.loads(path.read_text())["coverage"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 100:
        parser.error("rounds 必须在 1–100 之间")
    with tempfile.TemporaryDirectory(prefix="quant-refresh-bench-") as root:
        print(json.dumps(benchmark(Path(root), args.rounds), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
