"""读取标普 500 历史成分名单（GitHub fja05680/sp500 的
`S&P 500 Historical Components & Changes (Updated).csv`：列 date,tickers，tickers 为逗号分隔）。"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

from quant.strategies.universe import Constituents


def parse_sp500_history(text: str) -> Constituents:
    snaps: dict[date, frozenset[str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        tickers = frozenset(t.strip() for t in row["tickers"].split(",") if t.strip())
        snaps[date.fromisoformat(row["date"])] = tickers
    return Constituents(snaps)


def load_constituents(path: Path) -> Constituents:
    return parse_sp500_history(path.read_text(encoding="utf-8"))
