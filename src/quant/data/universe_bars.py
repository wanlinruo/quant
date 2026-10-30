"""候选池日线同步（组合策略设计 5.2）：动量仓排序用的约 500 只成分股。

- 数据源为新浪日 K（不占老虎历史日线额度）；新浪不做拆股调整，用老虎拆股记录换算到最新口径
  （adj_close = 调整后的收盘价，不含分红）。
- 存放在单独的 Parquet 目录（universe_bars），不与老虎复权数据混在一起。
- 拿不到的记入 missing；校验不通过的（如没有记录的拆股表现为单日 -50% 以上）记入 excluded
  并删除本地数据，不参与排序。两者合计使覆盖率低于 min_coverage 时整体失败。
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from quant.core.calendar import TradingCalendar
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider, DataUnavailableError
from quant.data.splits import split_adjust
from quant.data.store import ParquetStore
from quant.data.validate import Issue, Report, validate_bars

SplitSource = Callable[[Sequence[str], date, date], dict[str, list[tuple[date, float]]]]
UNIVERSE_MAX_MOVE = 0.4
MIN_COVERAGE = 0.95


def universe_store(data_dir: Path) -> ParquetStore:
    return ParquetStore(data_dir / "universe_bars")


@dataclass
class UniverseSync:
    fetched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)

    def coverage(self, n_members: int) -> float:
        if n_members == 0:
            return 0.0
        return (n_members - len(self.missing) - len(self.excluded)) / n_members


def _up_to_date(store: ParquetStore, symbol: str, start: date, end: date) -> bool:
    bars = store.read_bars(symbol, start, end)
    return len(bars) > 0 and pd.Timestamp(bars.index[-1]).date() >= end


def sync_universe_bars(
    store: ParquetStore,
    provider: DataProvider,
    splits: SplitSource | None,
    cal: TradingCalendar,
    members: Collection[str],
    start: date,
    end: date,
    max_move: float = UNIVERSE_MAX_MOVE,
    min_coverage: float = MIN_COVERAGE,
) -> UniverseSync:
    result = UniverseSync()
    todo: dict[str, pd.DataFrame] = {}
    for s in sorted(members):
        if _up_to_date(store, s, start, end):
            result.skipped.append(s)
            continue
        try:
            todo[s] = provider.daily_bars(s, start, end)
        except DataUnavailableError:
            result.missing.append(s)
    recorded = splits(sorted(todo), start, end) if splits is not None and todo else {}
    for s, raw in todo.items():
        bars = split_adjust(raw, recorded.get(s, [])) if splits is not None else raw
        first = max(start, pd.Timestamp(bars.index[0]).date())
        report = validate_bars(s, bars, cal, first, end, max_move=max_move)
        if not report.ok:
            result.excluded[s] = str(report.issues[0])
            store.delete(s)
            continue
        store.write_bars(s, bars, provider.source)
        result.fetched.append(s)
    cov = result.coverage(len(members))
    if cov < min_coverage:
        report = Report()
        report.extend(
            [Issue("候选池", "coverage", end, f"覆盖率 {cov:.1%} 低于 {min_coverage:.0%}")]
        )
        raise DataValidationError(report)
    return result
