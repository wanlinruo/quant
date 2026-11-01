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


# 公司名第一个词太泛时用前两个词（如 Bank of America → “Bank of”不够，取 “American Express”）
GENERIC_FIRST = {
    "american", "bank", "general", "home", "dollar", "first", "united", "international",
    "national", "global", "public", "federal", "southern", "western", "eastern", "northern",
    "digital", "universal", "consolidated", "texas", "pacific", "royal", "the",
}  # fmt: skip
# 标题里常用的叫法与公司正式名不同的
ALIASES: dict[str, tuple[str, ...]] = {
    "GOOGL": ("Google", "Alphabet"), "GOOG": ("Google", "Alphabet"), "DIS": ("Disney",),
    "MMM": ("3M",), "META": ("Meta", "Facebook"), "BRK.B": ("Berkshire",),
    "KO": ("Coca-Cola", "Coke"), "PG": ("Procter",), "JNJ": ("Johnson & Johnson", "J&J"),
}  # fmt: skip


def _plain(text: str) -> str:
    """去掉撇号与连字符，让 McDonald's / McDonalds、Coca-Cola / CocaCola 等价。"""
    return re.sub(r"[\'’\-]", "", text)


def short_name(name: str | None) -> str | None:
    """公司简称：去掉 Inc./Corp. 等后缀后的第一个词（太泛时取前两个词），至少 3 个字符。"""
    if not name:
        return None
    words = _plain(re.sub(r"\(.*?\)", " ", _SUFFIX_RE.sub(" ", name))).split()
    if not words:
        return None
    key = " ".join(words[:2]) if words[0].lower() in GENERIC_FIRST and len(words) >= 2 else words[0]
    key = re.sub(r"[^A-Za-z0-9& ]", "", key).strip()
    return key if len(key) >= 3 and re.search(r"[A-Za-z]{2}", key) else None


def mentions(headline: str, symbol: str, name: str | None) -> bool:
    """标题是否提到这只股票：代码（区分大小写；1 位代码须写 (X)、$X、:X）、公司简称或别名。"""
    plain = _plain(headline)
    if len(symbol) == 1:
        sym_hit = re.search(rf"(?:\(|\$|:\s?){re.escape(symbol)}(?![A-Za-z0-9])", headline)
    else:
        sym_hit = re.search(rf"(?<![A-Za-z0-9.]){re.escape(symbol)}(?![A-Za-z0-9])", headline)
    if sym_hit:
        return True
    keys = [k for k in (short_name(name), *ALIASES.get(symbol, ())) if k]
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(_plain(k))}(?![A-Za-z0-9])", plain, re.I)
               for k in keys)  # fmt: skip


def pick_reason(
    symbol: str, name: str | None, items: Iterable[NewsRaw], since: datetime
) -> Reason | None:
    hits = [x for x in items if x.time >= since and mentions(x.headline, symbol, name)]
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
    names: Mapping[str, str | None] | None = None,
) -> list[NewsItem]:
    """持仓公司新闻只收标题提到代码或公司简称的（Finnhub 公司新闻里有不少泛泛而谈的文章）。"""
    names = names or {}
    cands: list[tuple[NewsRaw, list[str], str]] = [(x, [], "macro") for x in market]
    for sym in sorted(holdings & set(company)):
        recent = sorted((x for x in company[sym]
                         if x.time >= since and mentions(x.headline, sym, names.get(sym))),
                        key=lambda x: x.time,
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
