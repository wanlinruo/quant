"""事件数据的读写（swing_pullback 设计第 6 节）。

- FOMC 决议日：包内数据文件 fomc_dates.csv（来源与整理日期见文件头），每年更新。
- 财报反应日：{data_dir}/events/earnings.csv（symbol,reaction_day），由
  `quant events sync-earnings` 从 SEC EDGAR 生成（data/providers/edgar.py），回测用。
- 实盘财报反应日：{data_dir}/events/earnings_live.csv，由每日任务从老虎财报日历同步（含未来日期）。
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

from quant.strategies.events import EventCalendar


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


def live_earnings_path(data_dir: Path) -> Path:
    return data_dir / "events" / "earnings_live.csv"


def sync_live_earnings(
    path: Path,
    fetch: Callable[[date, date], Mapping[str, Sequence[date]]],
    today: date,
    back_days: int = 400,
    ahead_days: int = 45,
) -> dict[str, tuple[date, ...]]:
    """拉取 [today - back_days, today + ahead_days] 的反应日并与本地文件合并保存。

    区间内以本次结果为准（公司改期时旧日期不残留）；区间外的旧日期保留（重放需要更早的历史）。
    """
    start, end = today - timedelta(days=back_days), today + timedelta(days=ahead_days)
    fresh = fetch(start, end)
    old = load_earnings(path) if path.exists() else {}
    merged: dict[str, set[date]] = {}
    for sym, days in old.items():
        merged[sym] = {d for d in days if d < start or d > end}
    for sym, new in fresh.items():
        merged.setdefault(sym, set()).update(new)
    out = {s: tuple(sorted(ds)) for s, ds in merged.items() if ds}
    save_earnings(path, out)
    return out


def load_events(data_dir: Path) -> EventCalendar:
    """实盘用的事件日历：earnings_live.csv ∪ earnings.csv（存在时）+ FOMC。"""
    earnings: dict[str, set[date]] = {}
    for p in (data_dir / "events" / "earnings.csv", live_earnings_path(data_dir)):
        if p.exists():
            for sym, days in load_earnings(p).items():
                earnings.setdefault(sym, set()).update(days)
    return EventCalendar(
        earnings={s: tuple(sorted(d)) for s, d in earnings.items()}, fomc=load_fomc_dates()
    )
