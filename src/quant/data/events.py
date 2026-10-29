"""事件数据的读写（swing_pullback 设计第 6 节）。

- FOMC 决议日：包内数据文件 fomc_dates.csv（来源与整理日期见文件头），每年更新。
- 财报反应日：{data_dir}/events/earnings.csv（symbol,reaction_day），由
  `quant events sync-earnings` 从 SEC EDGAR 生成（data/providers/edgar.py）。
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from datetime import date
from importlib import resources
from pathlib import Path


def load_fomc_dates(path: Path | None = None) -> tuple[date, ...]:
    """FOMC 例会决议日，升序、去重；默认读包内 fomc_dates.csv。"""
    if path is None:
        text = resources.files("quant.data").joinpath("fomc_dates.csv").read_text("utf-8")
    else:
        text = path.read_text("utf-8")
    days = {
        date.fromisoformat(line.strip())
        for line in text.splitlines()
        if line.strip() and not line.startswith("#") and line.strip() != "date"
    }
    return tuple(sorted(days))


def save_earnings(path: Path, data: Mapping[str, Sequence[date]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "reaction_day"])
        for sym in sorted(data):
            for d in sorted(set(data[sym])):
                w.writerow([sym, d.isoformat()])
    tmp.replace(path)


def load_earnings(path: Path) -> dict[str, tuple[date, ...]]:
    out: dict[str, set[date]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["symbol"], set()).add(date.fromisoformat(row["reaction_day"]))
    return {s: tuple(sorted(ds)) for s, ds in out.items()}
