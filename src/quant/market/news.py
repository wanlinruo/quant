"""首页新闻流与异动表“原因”列（#69 第 2 批）。纯计算，数据来自 Finnhub。

- 市场新闻 → macro（页面标“宏观”）；持仓的公司新闻 → company，标题像财报的 → earnings。
- 原因：标题里出现代码或公司简称（整词）的最新一条；不做翻译、不用 AI。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from quant.data.providers.finnhub import NewsRaw

EARNINGS_RE = re.compile(r"\b(earnings|results|quarter|guidance|eps|revenue)\b", re.I)
_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|plc|holdings|group|class [a-z])\b\.?",
    re.I,
)
PER_SYMBOL = 5  # 每只持仓最多几条公司新闻


@dataclass(frozen=True)
class NewsItem:
    time: str  # UTC ISO
    headline: str
    source: str
    url: str
    tags: list[str]
    category: str  # "macro" | "company" | "earnings"


@dataclass(frozen=True)
class Reason:
    headline: str
    source: str
    time: str  # UTC ISO
    url: str


def short_name(name: str | None) -> str | None:
    """公司简称：去掉 Inc./Corp. 等后缀后的第一个词，至少 3 个字母。"""
    if not name:
        return None
    words = _SUFFIX_RE.sub(" ", name).split()
    if not words:
        return None
    first = re.sub(r"[^A-Za-z]", "", words[0])
    return first if len(first) >= 3 else None


def _mentions(headline: str, symbol: str, name: str | None) -> bool:
    keys = [symbol, *([s] if (s := short_name(name)) else [])]
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(k)}(?![A-Za-z0-9])", headline, re.I)
               for k in keys)  # fmt: skip


def pick_reason(
    symbol: str, name: str | None, items: Iterable[NewsRaw], since: datetime
) -> Reason | None:
    hits = [x for x in items if x.time >= since and _mentions(x.headline, symbol, name)]
    if not hits:
        return None
    x = max(hits, key=lambda x: x.time)
    return Reason(x.headline, x.source, x.time.isoformat(), x.url)


def build_feed(
    market: Iterable[NewsRaw],
    company: Mapping[str, Iterable[NewsRaw]],
    holdings: set[str],
    since: datetime,
    limit: int = 80,
) -> list[NewsItem]:
    cands: list[tuple[NewsRaw, list[str], str]] = [(x, [], "macro") for x in market]
    for sym in sorted(holdings & set(company)):
        recent = sorted((x for x in company[sym] if x.time >= since), key=lambda x: x.time,
                        reverse=True)[:PER_SYMBOL]  # fmt: skip
        cands += [(x, [sym], "earnings" if EARNINGS_RE.search(x.headline) else "company")
                  for x in recent]  # fmt: skip
    cands.sort(key=lambda c: c[0].time, reverse=True)
    out: list[NewsItem] = []
    seen: set[str] = set()
    for x, tags, cat in cands:
        key = x.headline.strip().lower()
        if x.time < since or key in seen:
            continue
        seen.add(key)
        out.append(NewsItem(x.time.isoformat(), x.headline, x.source, x.url, tags, cat))
        if len(out) >= limit:
            break
    return out
