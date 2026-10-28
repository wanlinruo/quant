"""行情数据校验（PROJECT_PLAN 7.3）。任一问题都会使 ok 为 False，后续信号任务不得运行。

检查项：
- 交易日缺失或多出非交易日（对照 XNYS 日历）
- 价格非正或为空，开高低收不自洽
- 单日涨跌幅异常（ETF 默认 15%，人工确认后可通过 acknowledged 放行）
- 数据不新鲜（最后一天早于预期交易日）
- 两个数据源抽样比对（同口径收盘价，默认 0.1%，见 Phase 0 报告 0.3）
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from quant.core.calendar import TradingCalendar

DEFAULT_MAX_DAILY_MOVE = 0.15
DEFAULT_SOURCE_TOLERANCE = 0.001
DEFAULT_SAMPLE_DAYS = 20


@dataclass(frozen=True)
class Issue:
    symbol: str
    kind: str  # missing_day / extra_day / bad_price / ohlc / jump / stale / source_diff / empty
    day: date | None
    detail: str

    def __str__(self) -> str:
        return f"[{self.symbol}] {self.kind} {self.day or ''} {self.detail}".replace("  ", " ")


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def extend(self, issues: list[Issue]) -> None:
        self.issues.extend(issues)

    def summary(self, limit: int = 20) -> str:
        if self.ok:
            return "校验通过"
        lines = [str(i) for i in self.issues[:limit]]
        if len(self.issues) > limit:
            lines.append(f"……共 {len(self.issues)} 个问题")
        return "\n".join(lines)


def _days(df: pd.DataFrame) -> list[date]:
    return [ts.date() for ts in pd.DatetimeIndex(df.index)]


def check_trading_days(
    symbol: str, bars: pd.DataFrame, cal: TradingCalendar, start: date, end: date
) -> list[Issue]:
    have = set(_days(bars))
    expected = set(cal.trading_days(start, end))
    issues = [Issue(symbol, "missing_day", d, "缺少交易日") for d in sorted(expected - have)]
    extra = sorted(d for d in have - expected if start <= d <= end)
    issues += [Issue(symbol, "extra_day", d, "非交易日出现数据") for d in extra]
    return issues


def check_prices(symbol: str, bars: pd.DataFrame) -> list[Issue]:
    issues: list[Issue] = []
    ohlc = bars[["open", "high", "low", "close"]]
    bad = ohlc.isna().any(axis=1) | (ohlc <= 0).any(axis=1)
    # adj_close 整列为空表示数据源不提供（如老虎），部分为空或非正才是问题
    adj = bars["adj_close"]
    if adj.notna().any():
        bad |= adj.isna() | (adj <= 0)
    for ts in bars.index[bad]:
        issues.append(Issue(symbol, "bad_price", ts.date(), "价格为空或非正"))
    ok = bars[~bad]
    inconsistent = (ok["high"] < ok[["open", "close"]].max(axis=1)) | (
        ok["low"] > ok[["open", "close"]].min(axis=1)
    )
    for ts in ok.index[inconsistent]:
        issues.append(Issue(symbol, "ohlc", ts.date(), "开高低收不自洽"))
    return issues


def check_jumps(
    symbol: str,
    bars: pd.DataFrame,
    max_move: float = DEFAULT_MAX_DAILY_MOVE,
    acknowledged: Collection[date] = (),
) -> list[Issue]:
    """单日涨跌幅超过阈值；用含分红复权价，没有时用收盘价。"""
    price = bars["adj_close"] if bars["adj_close"].notna().all() else bars["close"]
    ret = price.pct_change()
    issues = []
    flagged = ret[ret.abs() > max_move]
    for ts, r in zip(pd.DatetimeIndex(flagged.index), flagged, strict=True):
        d = ts.date()
        if d not in acknowledged:
            issues.append(
                Issue(symbol, "jump", d, f"单日涨跌 {r:+.2%} 超过 {max_move:.0%}，需人工确认")
            )
    return issues


def check_freshness(symbol: str, bars: pd.DataFrame, expected_last: date) -> list[Issue]:
    last = _days(bars)[-1] if len(bars) else None
    if last is None or last < expected_last:
        return [Issue(symbol, "stale", last, f"最新数据早于 {expected_last}")]
    return []


def compare_sources(
    symbol: str,
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    tolerance: float = DEFAULT_SOURCE_TOLERANCE,
    sample_days: int = DEFAULT_SAMPLE_DAYS,
) -> list[Issue]:
    """比较两个数据源最近 sample_days 个共同交易日的收盘价（均为仅拆股调整口径）。"""
    common = primary.index.intersection(secondary.index).sort_values()[-sample_days:]
    if len(common) == 0:
        return [Issue(symbol, "source_diff", None, "两个数据源没有共同日期")]
    a, b = primary.loc[common, "close"], secondary.loc[common, "close"]
    rel = ((a - b).abs() / b).astype(float)
    over = rel[rel > tolerance]
    return [
        Issue(symbol, "source_diff", ts.date(), f"收盘价相差 {r:.3%} 超过 {tolerance:.2%}")
        for ts, r in zip(pd.DatetimeIndex(over.index), over, strict=True)
    ]


def validate_bars(
    symbol: str,
    bars: pd.DataFrame,
    cal: TradingCalendar,
    start: date,
    end: date,
    *,
    max_move: float = DEFAULT_MAX_DAILY_MOVE,
    acknowledged_jumps: Collection[date] = (),
) -> Report:
    """单个标的 [start, end] 区间的完整校验；end 即预期的最新交易日。"""
    report = Report()
    window = bars[(bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))]
    if window.empty:
        report.extend([Issue(symbol, "empty", None, f"{start}~{end} 没有数据")])
        return report
    report.extend(check_trading_days(symbol, window, cal, start, end))
    report.extend(check_prices(symbol, window))
    report.extend(check_jumps(symbol, window, max_move, acknowledged_jumps))
    expected_last = end if cal.is_trading_day(end) else cal.previous_trading_day(end)
    report.extend(check_freshness(symbol, window, expected_last))
    return report
