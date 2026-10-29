"""事件数据的读写（swing_pullback 设计第 6 节）。

- FOMC 决议日：包内数据文件 fomc_dates.csv（来源与整理日期见文件头），每年更新。
"""

from __future__ import annotations

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
