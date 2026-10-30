"""候选池日线同步（组合策略设计 5.2）：动量仓排序用的约 500 只成分股。

- 数据源为新浪日 K（不占老虎历史日线额度）；新浪不做拆股调整，用老虎拆股记录换算到最新口径
  （adj_close = 调整后的收盘价，不含分红）。
- 存放在单独的 Parquet 目录（universe_bars），不与老虎复权数据混在一起。
- 拿不到的记入 missing；校验不通过的（单日涨跌超过 60%，如没有记录的合股）记入 excluded
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
# 与回测一致（scripts/research/combo.py MAX_MOVE）：真实财报跳空可达 40–60%；
# 未记录的合股（价格 ×2 以上）仍会被识别。未记录的拆股表现为暴跌，只会让该股排名靠后，不会被误选。
UNIVERSE_MAX_MOVE = 0.6
MIN_COVERAGE = 0.95


def universe_store(data_dir: Path) -> ParquetStore:
    return ParquetStore(data_dir / "universe_bars")


def frozen_members_path(data_dir: Path, rebalance: date) -> Path:
    return data_dir / "universe" / f"members_{rebalance.isoformat()}.txt"


def freeze_members(data_dir: Path, rebalance: date, members: Collection[str]) -> None:
    """调仓日候选池同步成功后冻结当日成分股：月内排序输入不再变化（与回测一致）。"""
    path = frozen_members_path(data_dir, rebalance)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(sorted(members)) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_frozen_members(data_dir: Path, rebalance: date) -> frozenset[str] | None:
    path = frozen_members_path(data_dir, rebalance)
    if not path.exists():
        return None
    return frozenset(
        line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


class UniverseNotReady(DataValidationError):
    """多数缺失是因为数据源还没有截至 end 的数据（当日未出）：可稍后重试。"""


@dataclass
class UniverseSync:
    fetched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    not_ready: list[str] = field(default_factory=list)  # 最新数据早于 end；计入 missing

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
        if raw.empty or pd.Timestamp(raw.index[-1]).date() < end:
            # 当日数据未出：不校验、不删除本地历史，记为缺失
            result.not_ready.append(s)
            result.missing.append(s)
            continue
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
        if len(result.not_ready) * 2 >= len(result.missing) + len(result.excluded):
            raise UniverseNotReady(report)
        raise DataValidationError(report)
    return result
