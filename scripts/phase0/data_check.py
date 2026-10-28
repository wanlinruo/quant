"""Phase 0 任务 0.3：回测数据质量检查（yfinance vs 老虎日线）。

拉取 5 个标的 10 年以上日线，按日期对齐，比较同口径的收盘价：
yfinance `Close`（auto_adjust=False，仅拆股调整）vs 老虎 `get_bars` 前复权收盘价
（实测仅拆股调整，不含分红，见 REPORT.md 0.2）。
另输出 yfinance `Adj Close` 与 `Close` 的差距，说明分红复权对收益率的影响。

只读，不下单。环境变量 TIGER_PROPS_PATH 同 tiger_check.py。

用法：
    uv run --env-file .env python scripts/phase0/data_check.py [--years 12]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd

SYMBOLS = ["SPY", "QQQ", "TLT", "GLD", "SHY"]
ET = ZoneInfo("America/New_York")

# 通过标准（相对误差）
MIN_COVERAGE = 0.99  # 双方共有日期 / yfinance 日期数
MAX_MEDIAN_DIFF = 0.0001  # 0.01%
MAX_P99_DIFF = 0.001  # 0.1%
MAX_ABS_DIFF = 0.01  # 1%


@dataclass(frozen=True)
class Comparison:
    symbol: str
    n_yf: int
    n_tiger: int
    n_common: int
    only_yf: list[date]
    only_tiger: list[date]
    median_diff: float
    p99_diff: float
    max_diff: float
    max_diff_date: date | None
    n_over_01pct: int

    @property
    def coverage(self) -> float:
        return self.n_common / self.n_yf if self.n_yf else 0.0

    def failures(self) -> list[str]:
        out = []
        if self.coverage < MIN_COVERAGE:
            out.append(f"覆盖率 {self.coverage:.2%} < {MIN_COVERAGE:.0%}")
        if self.median_diff > MAX_MEDIAN_DIFF:
            out.append(f"中位误差 {self.median_diff:.4%} > {MAX_MEDIAN_DIFF:.2%}")
        if self.p99_diff > MAX_P99_DIFF:
            out.append(f"P99 误差 {self.p99_diff:.4%} > {MAX_P99_DIFF:.1%}")
        if self.max_diff > MAX_ABS_DIFF:
            out.append(f"最大误差 {self.max_diff:.4%} > {MAX_ABS_DIFF:.0%}")
        return out


def compare_closes(symbol: str, tiger: pd.Series, yf: pd.Series) -> Comparison:
    """tiger、yf 均为以 date 为索引的收盘价序列。"""
    tiger = tiger.dropna()
    yf = yf.dropna()
    common = tiger.index.intersection(yf.index)
    rel = ((tiger.loc[common] - yf.loc[common]).abs() / yf.loc[common]).astype(float)
    has = len(rel) > 0
    return Comparison(
        symbol=symbol,
        n_yf=len(yf),
        n_tiger=len(tiger),
        n_common=len(common),
        only_yf=sorted(yf.index.difference(tiger.index)),
        only_tiger=sorted(tiger.index.difference(yf.index)),
        median_diff=float(rel.median()) if has else float("nan"),
        p99_diff=float(rel.quantile(0.99)) if has else float("nan"),
        max_diff=float(rel.max()) if has else float("nan"),
        max_diff_date=cast(date, rel.idxmax()) if has else None,
        n_over_01pct=int((rel > 0.001).sum()),
    )


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp() * 1000)


def fetch_tiger(symbols: list[str], start: date, end: date) -> dict[str, pd.Series]:
    from tigeropen.common.consts import BarPeriod, QuoteRight
    from tigeropen.quote.quote_client import QuoteClient
    from tigeropen.tiger_open_config import TigerOpenClientConfig

    props = os.environ.get("TIGER_PROPS_PATH")
    if not props:
        raise RuntimeError("未设置 TIGER_PROPS_PATH")
    cfg = TigerOpenClientConfig(props_path=str(Path(props).expanduser()) + os.sep)
    client = QuoteClient(cfg)
    out = {}
    for sym in symbols:
        df = client.get_bars_by_page(
            sym,
            period=BarPeriod.DAY,
            begin_time=_ms(start),
            end_time=_ms(end + timedelta(days=1)),
            total=10000,
            page_size=1000,
            right=QuoteRight.BR,
        )
        dates = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(ET).dt.date
        out[sym] = pd.Series(df["close"].to_numpy(), index=dates).sort_index()
        out[sym] = out[sym][~out[sym].index.duplicated()]
        time.sleep(1.1)  # kline 60 次/分钟
    return out


def fetch_yf(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out = {}
    for sym in symbols:
        for attempt in range(3):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    df = yf.download(
                        sym,
                        start=start.isoformat(),
                        end=(end + timedelta(days=1)).isoformat(),
                        auto_adjust=False,
                        progress=False,
                        multi_level_index=False,
                    )
                if df is None or df.empty:
                    raise RuntimeError("返回为空")
                df.index = [ts.date() for ts in df.index]
                out[sym] = df
                break
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"yfinance {sym} 拉取失败：{e}") from e
                time.sleep(2 * (attempt + 1))
    return out


def _fmt_dates(ds: list[date], n: int = 5) -> str:
    s = ", ".join(d.isoformat() for d in ds[:n])
    return s + (f" …（共 {len(ds)} 个）" if len(ds) > n else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=12)
    args = ap.parse_args()
    end = datetime.now(ET).date() - timedelta(days=1)
    start = end.replace(year=end.year - args.years)
    print(f"区间：{start} ~ {end}，标的：{', '.join(SYMBOLS)}")

    yf_data = fetch_yf(SYMBOLS, start, end)
    tiger_data = fetch_tiger(SYMBOLS, start, end)

    failed = False
    for sym in SYMBOLS:
        yf_df = yf_data[sym]
        c = compare_closes(sym, tiger_data[sym], yf_df["Close"])
        print(f"\n== {sym} ==")
        print(
            f"  yfinance {c.n_yf} 天（{min(yf_df.index)} 起），老虎 {c.n_tiger} 天"
            f"（{min(tiger_data[sym].index) if c.n_tiger else '-'} 起），共有 {c.n_common} 天，"
            f"覆盖率 {c.coverage:.2%}"
        )
        if c.only_yf:
            print(f"  仅 yfinance 有：{_fmt_dates(c.only_yf)}")
        if c.only_tiger:
            print(f"  仅老虎有：{_fmt_dates(c.only_tiger)}")
        print(
            f"  收盘价相对误差：中位 {c.median_diff:.4%}，P99 {c.p99_diff:.4%}，"
            f"最大 {c.max_diff:.4%}（{c.max_diff_date}），>0.1% 的天数 {c.n_over_01pct}"
        )
        close, adj = yf_df["Close"], yf_df["Adj Close"]
        r_close = close.iloc[-1] / close.iloc[0] - 1
        r_adj = adj.iloc[-1] / adj.iloc[0] - 1
        print(
            f"  区间收益：仅拆股调整 {r_close:.2%}，含分红复权 {r_adj:.2%}，"
            f"差 {r_adj - r_close:.2%}"
        )
        fails = c.failures()
        print(f"  结论：{'通过' if not fails else '不通过 - ' + '；'.join(fails)}")
        failed |= bool(fails)

    print(f"\n总体：{'不通过' if failed else '通过'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
