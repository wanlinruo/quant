"""首页市场看板的数据（#69）：标普 500 GICS 成分表、Massive 全市场日线（按日存）、市值。

都在 data_dir/market 下；数据来自免费档，仅个人使用，不提交到仓库。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import http_get_text
from quant.data.quota import atomic_write_text

log = logging.getLogger("quant.market_store")

MARKET_DIR = "market"
EMPTY_DAY_RETRY_DAYS = 7
GICS_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
)
GICS_MIN_ROWS = 400  # 少于这个数视为下载不完整
GICS_COLUMNS = ("Symbol", "Security", "GICS Sector")

# 代码 → （中文名，组）。指数用 ETF 代理（页面注明 ETF）
ETF_SET: dict[str, tuple[str, str]] = {
    "SPY": ("标普 500", "index"), "QQQ": ("纳指 100", "index"), "DIA": ("道指", "index"),
    "IWM": ("罗素 2000", "index"), "GLD": ("黄金", "other"),
    "XLK": ("信息技术", "sector"), "XLF": ("金融", "sector"), "XLV": ("医疗保健", "sector"),
    "XLY": ("可选消费", "sector"), "XLP": ("必需消费", "sector"), "XLE": ("能源", "sector"),
    "XLI": ("工业", "sector"), "XLB": ("原材料", "sector"), "XLU": ("公用事业", "sector"),
    "XLRE": ("房地产", "sector"), "XLC": ("通信服务", "sector"),
    "IWF": ("罗素 1000 成长", "style"), "IWD": ("罗素 1000 价值", "style"),
    "RSP": ("标普 500 等权", "style"), "MTUM": ("动量因子", "style"),
    "SPHB": ("标普 500 高贝塔", "style"), "SPLV": ("标普 500 低波动", "style"),
}  # fmt: skip

# GICS 板块（数据集中的英文名）→ 板块 ETF
SECTOR_ETF: dict[str, str] = {
    "Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE",
    "Communication Services": "XLC",
}  # fmt: skip

# GICS 板块（英文）→ 中文名（取板块 ETF 的名称）
SECTOR_NAME = {sec: ETF_SET[etf][0] for sec, etf in SECTOR_ETF.items()}


def market_dir(data_dir: Path) -> Path:
    return data_dir / MARKET_DIR


def snapshot_path(data_dir: Path) -> Path:
    """market_data 任务生成、面板只读的首页快照。"""
    return market_dir(data_dir) / "snapshot.json"


def intraday_snapshot_path(data_dir: Path) -> Path:
    """quotes 服务盘中生成的首页快照（#93）：最后一天为盘中价格拼出的当日 K 线。"""
    return market_dir(data_dir) / "snapshot_intraday.json"


# market_extras 任务生成、面板只读的文件（#69 第 2 批）
def macro_path(data_dir: Path) -> Path:
    return market_dir(data_dir) / "macro.json"


def news_path(data_dir: Path) -> Path:
    return market_dir(data_dir) / "news.json"


def calendar_path(data_dir: Path) -> Path:
    return market_dir(data_dir) / "calendar.json"


def ff_events_path(data_dir: Path) -> Path:
    return market_dir(data_dir) / "ff_events.json"


STOCK_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


def stock_path(data_dir: Path, symbol: str) -> Path | None:
    """个股新闻与财务指标文件（stock_data 任务写、个股页接口读）；代码不合法时为 None。"""
    if not STOCK_SYMBOL_RE.match(symbol):
        return None
    return market_dir(data_dir) / "stocks" / f"{symbol}.json"


def gics_path(data_dir: Path) -> Path:
    return market_dir(data_dir) / "sp500_gics.csv"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class GicsTable:
    rows: dict[str, tuple[str, str]]  # 代码 → （公司名，GICS 板块）
    sub: dict[str, str] = field(default_factory=dict)  # 代码 → GICS 子行业（英文，可能缺）

    @property
    def members(self) -> list[str]:
        return sorted(self.rows)

    def sector(self, symbol: str) -> str | None:
        r = self.rows.get(symbol)
        return r[1] if r else None

    def name(self, symbol: str) -> str | None:
        r = self.rows.get(symbol)
        return r[0] if r else None

    def sub_industry(self, symbol: str) -> str | None:
        return self.sub.get(symbol) or None


def parse_gics(text: str) -> GicsTable:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or any(c not in reader.fieldnames for c in GICS_COLUMNS):
        raise ValueError("成分表缺少 Symbol / Security / GICS Sector 列")
    rows: dict[str, tuple[str, str]] = {}
    subs: dict[str, str] = {}
    for r in reader:
        sym = (r.get("Symbol") or "").strip()
        if not sym:
            continue
        rows[sym] = (r["Security"].strip(), r["GICS Sector"].strip())
        if si := (r.get("GICS Sub-Industry") or "").strip():
            subs[sym] = si
    return GicsTable(rows, subs)


def load_gics(data_dir: Path) -> GicsTable | None:
    """只读本地成分表（不下载）；没有或无法解析时为 None。面板接口用。"""
    try:
        return parse_gics(gics_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, csv.Error):
        return None


def sync_gics(
    data_dir: Path,
    today: date,
    fetch_text: Callable[[str], str] = http_get_text,
    max_age_days: int = 7,
) -> GicsTable:
    """文件超过 max_age_days 天才重新下载；下载或解析失败时沿用旧文件，没有旧文件则报错。"""
    path = gics_path(data_dir)
    if path.exists() and time.time() - path.stat().st_mtime < max_age_days * 86400:
        return parse_gics(path.read_text(encoding="utf-8"))
    try:
        text = fetch_text(GICS_URL)
        table = parse_gics(text)
        if len(table.rows) < GICS_MIN_ROWS:
            raise ValueError(f"成分表只有 {len(table.rows)} 行")
    except (OSError, ValueError) as e:
        if path.exists():
            log.warning("标普 500 成分表更新失败，沿用旧文件：%s", type(e).__name__)
            return parse_gics(path.read_text(encoding="utf-8"))
        raise DataUnavailableError(f"拿不到标普 500 成分表：{type(e).__name__}") from None
    atomic_write_text(path, text)
    return table


@dataclass(frozen=True)
class CapInfo:
    market_cap: float | None
    name: str | None
    updated: date


class MarketStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = market_dir(data_dir)
        self._daily = self.root / "daily"

    def _day_path(self, day: date) -> Path:
        return self._daily / f"{day.isoformat()}.parquet"

    def write_day(self, day: date, frame: pd.DataFrame, keep: Iterable[str]) -> int:
        """只保留 keep 中的代码，原子写入；返回写入的行数。"""
        wanted = set(keep)
        df = frame[frame["symbol"].isin(wanted)].reset_index(drop=True)
        self._daily.mkdir(parents=True, exist_ok=True)
        path = self._day_path(day)
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
        self.clear_empty(day)
        return len(df)

    def has_day(self, day: date) -> bool:
        return self._day_path(day).exists()

    def days(self) -> list[date]:
        if not self._daily.is_dir():
            return []
        return sorted(date.fromisoformat(p.stem) for p in self._daily.glob("*.parquet"))

    def panel(
        self, field: str, symbols: Sequence[str] | None = None, last_n: int | None = None
    ) -> pd.DataFrame:
        """日期 × 代码的宽表（索引为 DatetimeIndex）。"""
        days = self.days()
        if last_n is not None:
            days = days[-last_n:]
        parts = []
        for d in days:
            df = pd.read_parquet(self._day_path(d), columns=["symbol", field])
            if symbols is not None:
                df = df[df["symbol"].isin(symbols)]
            df["date"] = pd.Timestamp(d)
            parts.append(df)
        if not parts:
            return pd.DataFrame()
        long = pd.concat(parts, ignore_index=True)
        wide = long.pivot_table(index="date", columns="symbol", values=field, aggfunc="last")
        wide.columns.name = None
        return wide.sort_index()

    def apply_split(self, symbol: str, before: date, factor: float) -> int:
        """把 before 之前已存的 symbol 日线按拆股系数换算（价格 × factor，成交量 ÷ factor）。

        Massive 的 adjusted=true 只在取数当时生效，已存的文件不会随之更新。返回改写的天数。
        """
        n = 0
        for d in self.days():
            if d >= before:
                break
            path = self._day_path(d)
            df = pd.read_parquet(path)
            hit = df["symbol"] == symbol
            if not hit.any():
                continue
            for c in ("open", "high", "low", "close", "vwap"):
                if c in df.columns:
                    df.loc[hit, c] = df.loc[hit, c] * factor
            if "volume" in df.columns:
                df.loc[hit, "volume"] = df.loc[hit, "volume"] / factor
            tmp = path.with_suffix(".tmp")
            df.to_parquet(tmp, index=False)
            tmp.replace(path)
            n += 1
        return n

    def coverage(self, day: date, members: Sequence[str]) -> float:
        """day 的日线中成分股所占比例。"""
        if not members or not self.has_day(day):
            return 0.0
        have = set(pd.read_parquet(self._day_path(day), columns=["symbol"])["symbol"])
        return len(have & set(members)) / len(members)

    def empty_day_records(self) -> dict[date, dict[str, str | int | None]]:
        """空日标记及来源；旧版永久列表读作待复核记录。"""
        doc = _read_json(self.root / "empty_days.json")
        if isinstance(doc, list):
            return {
                date.fromisoformat(str(d)): {
                    "source": "legacy",
                    "marked_on": None,
                    "retry_after": None,
                    "attempts": 1,
                }
                for d in doc
            }
        rows = doc.get("days") if isinstance(doc, dict) else None
        if not isinstance(rows, dict):
            return {}
        out: dict[date, dict[str, str | int | None]] = {}
        for raw_day, raw in rows.items():
            if not isinstance(raw, dict):
                continue
            try:
                d = date.fromisoformat(str(raw_day))
            except ValueError:
                continue
            out[d] = {
                "source": str(raw.get("source") or "unknown"),
                "marked_on": str(raw["marked_on"]) if raw.get("marked_on") else None,
                "retry_after": str(raw["retry_after"]) if raw.get("retry_after") else None,
                "attempts": int(raw.get("attempts") or 1),
            }
        return out

    def empty_days(self, as_of: date | None = None) -> set[date]:
        """返回仍在冷却期的空日；不传 as_of 时返回全部记录供状态展示。"""
        rows = self.empty_day_records()
        if as_of is None:
            return set(rows)
        active = set()
        for day, row in rows.items():
            raw = row.get("retry_after")
            try:
                retry_after = date.fromisoformat(str(raw)) if raw else date.min
            except ValueError:
                retry_after = date.min
            if as_of < retry_after:
                active.add(day)
        return active

    def mark_empty(
        self,
        day: date,
        *,
        source: str,
        observed_on: date,
        retry_days: int = EMPTY_DAY_RETRY_DAYS,
    ) -> None:
        rows = self.empty_day_records()
        previous = rows.get(day, {})
        rows[day] = {
            "source": source,
            "marked_on": observed_on.isoformat(),
            "retry_after": (observed_on + timedelta(days=retry_days)).isoformat(),
            "attempts": int(previous.get("attempts") or 0) + 1,
        }
        doc = {"version": 2, "days": {d.isoformat(): rows[d] for d in sorted(rows)}}
        atomic_write_text(self.root / "empty_days.json", json.dumps(doc, ensure_ascii=False))

    def clear_empty(self, day: date) -> None:
        path = self.root / "empty_days.json"
        rows = self.empty_day_records()
        if day not in rows:
            return
        rows.pop(day)
        doc = {"version": 2, "days": {d.isoformat(): rows[d] for d in sorted(rows)}}
        atomic_write_text(path, json.dumps(doc, ensure_ascii=False))

    def splits_checked(self) -> date | None:
        """拆股已核对到的日期（含）。"""
        path = self.root / "splits.json"
        if not path.exists():
            return None
        return date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["checked"])

    def set_splits_checked(self, day: date) -> None:
        atomic_write_text(self.root / "splits.json", json.dumps({"checked": day.isoformat()}))

    def caps(self) -> dict[str, CapInfo]:
        path = self.root / "caps.json"
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: CapInfo(v["market_cap"], v["name"], date.fromisoformat(v["updated"]))
                for k, v in raw.items()}  # fmt: skip

    def write_caps(self, caps: dict[str, CapInfo]) -> None:
        doc = {k: {"market_cap": v.market_cap, "name": v.name, "updated": v.updated.isoformat()}
               for k, v in sorted(caps.items())}  # fmt: skip
        atomic_write_text(self.root / "caps.json", json.dumps(doc, ensure_ascii=False))
