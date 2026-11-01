"""每日任务 stock_data 的主体（#70 第 3 批）：为“标普 500 成分 ∪ 持仓”预取公司新闻与财务指标。

Finnhub 每分钟 60 次（客户端间隔 1.1 秒）：新闻每只每天 1 次，财务指标每天最旧的 metrics_per_run 只
（约一周一轮）。持仓优先，其余按上次更新时间从旧到新；限流或临时故障时停止剩余请求、保留已写文件，
下次从最旧的继续；key 无效时抛出。每只写一个 market/stocks/{SYMBOL}.json，面板只读。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from quant.data.market_store import ETF_SET, STOCK_SYMBOL_RE, load_gics, market_dir, stock_path
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.finnhub import FinnhubAuthError, NewsRaw
from quant.data.quota import atomic_write_text
from quant.market.news import EARNINGS_RE, NewsItem, _mentions
from quant.market.stock_metrics import METRIC_KEYS

log = logging.getLogger("quant.stock_data")

NEWS_DAYS = 7
NEWS_KEEP = 15
METRICS_MAX_AGE = 6  # 天；超过才重取


class FinnhubLike(Protocol):
    def company_news(self, symbol: str, start: date, end: date) -> list[NewsRaw]: ...
    def metrics(self, symbol: str, keys: Sequence[str]) -> dict[str, float | None]: ...


def _load(path: Path) -> dict[str, Any]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _items(symbol: str, name: str | None, raw: list[NewsRaw]) -> list[dict[str, Any]]:
    """只保留标题提到代码或公司简称的新闻（去掉“5 Best Stocks…”这类泛泛而谈的文章）。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for x in sorted(raw, key=lambda x: x.time, reverse=True):
        key = x.headline.strip().lower()
        if key in seen or not _mentions(x.headline, symbol, name):
            continue
        seen.add(key)
        cat = "earnings" if EARNINGS_RE.search(x.headline) else "company"
        out.append(asdict(NewsItem(x.time.isoformat(), x.headline, x.source, x.url, [symbol], cat)))
        if len(out) >= NEWS_KEEP:
            break
    return out


def run_stock_data(
    *,
    data_dir: Path,
    today: date,
    now: datetime,
    holdings: set[str],
    finnhub: FinnhubLike,
    metrics_per_run: int = 120,
) -> str:
    gics = load_gics(data_dir)
    members = set(gics.members) if gics else set()
    universe = sorted(s for s in members | holdings
                      if s not in ETF_SET and STOCK_SYMBOL_RE.match(s))  # fmt: skip
    docs: dict[str, dict[str, Any]] = {}
    for s in universe:
        p = stock_path(data_dir, s)
        docs[s] = _load(p) if p else {}

    def order(field: str) -> list[str]:
        return sorted(universe, key=lambda s: (s not in holdings, docs[s].get(field) or "", s))

    def save(s: str) -> None:
        p = stock_path(data_dir, s)
        if p is not None:
            docs[s]["symbol"] = s
            atomic_write_text(p, json.dumps(docs[s], ensure_ascii=False, allow_nan=False))

    notes: list[str] = []
    news_n = metrics_n = 0
    stage = "财务指标"
    try:
        # 指标先取（每天最多 metrics_per_run 次），新闻阶段限流时指标仍有进展
        cutoff = (today - timedelta(days=METRICS_MAX_AGE)).isoformat()
        due = [s for s in order("metrics_updated")
               if (docs[s].get("metrics_updated") or "") < cutoff]  # fmt: skip
        for s in due[:metrics_per_run]:
            docs[s]["metrics"] = finnhub.metrics(s, METRIC_KEYS)
            docs[s]["metrics_updated"] = today.isoformat()
            save(s)
            metrics_n += 1
        stage = "新闻"
        for s in order("news_updated"):
            raw = finnhub.company_news(
                s, today - timedelta(days=NEWS_DAYS), today + timedelta(days=1)
            )
            docs[s]["news"] = _items(s, gics.name(s) if gics else None, raw)
            docs[s]["news_updated"] = now.isoformat()
            save(s)
            news_n += 1
    except FinnhubAuthError:
        raise
    except DataUnavailableError as e:
        notes.append(f"限流或故障，停在{stage}阶段（{e}）")
    removed = 0
    if gics is not None:  # 读不到成分表时一个都不删（避免误删全部）
        keep = set(universe)
        for p in (market_dir(data_dir) / "stocks").glob("*.json"):
            if p.stem not in keep:
                p.unlink(missing_ok=True)  # 移出标普 500 或已卖出：可再生成，直接删
                removed += 1
    if removed:
        notes.append(f"清理停用代码 {removed} 只")
    summary = f"新闻 {news_n} 只；指标 {metrics_n} 只；覆盖 {len(universe)} 只"
    return "；".join([summary, *notes])
