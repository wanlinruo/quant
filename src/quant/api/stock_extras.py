"""个股页估值与财务、新闻、同板块（#70 第 3 批）：只读 stock_data 的个股文件、GICS 表、市值与快照。

只覆盖“标普 500 成分 ∪ 持仓”；其他代码返回 covered=False（面板写明覆盖范围），不是错误。
任何文件缺失或损坏都降级为空，不返回 500。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from quant.api.schemas import MetricOut, NewsItemOut, PeerOut, StockExtras
from quant.data.market_store import (
    ETF_SET,
    SECTOR_ETF,
    SECTOR_NAME,
    STOCK_SYMBOL_RE,
    MarketStore,
    load_gics,
    snapshot_path,
    stock_path,
)
from quant.market.stock_metrics import METRICS

log = logging.getLogger("quant.api.stock_extras")
PEERS = 8


def _load(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("%s 无法读取：%s", path.name, type(e).__name__)
        return {}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _rets(snap: dict[str, Any], period: str) -> dict[str, float | None]:
    heat = snap.get("heatmap")
    cells = heat.get(period) if isinstance(heat, dict) else None
    try:
        return {c["symbol"]: _num(c.get("ret")) for c in cells or []}
    except (TypeError, KeyError, AttributeError):
        return {}


def _caps(data_dir: Path) -> dict[str, float | None]:
    try:
        return {k: _num(v.market_cap) for k, v in MarketStore(data_dir).caps().items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log.warning("caps.json 无法读取：%s", type(e).__name__)
        return {}


def stock_extras(data_dir: Path, symbol: str) -> StockExtras:
    sym = symbol.upper()
    empty = StockExtras(symbol=sym, covered=False)
    if not STOCK_SYMBOL_RE.match(sym) or sym in ETF_SET:
        return empty
    gics = load_gics(data_dir)
    doc = _load(stock_path(data_dir, sym))
    in_gics = gics is not None and sym in gics.rows
    if not in_gics and not doc:
        return empty
    caps = _caps(data_dir)
    out: dict[str, Any] = {"symbol": sym, "covered": True, "in_sp500": in_gics,
                           "market_cap": caps.get(sym)}  # fmt: skip
    if gics is not None and in_gics:
        sector = gics.sector(sym)
        etf = SECTOR_ETF.get(sector or "")
        snap = _load(snapshot_path(data_dir))
        sp = snap.get("sector_periods")
        raw_periods = sp.get(etf) if etf and isinstance(sp, dict) else None
        periods = ({k: v for k, v in raw_periods.items() if _num(v) is not None}
                   if isinstance(raw_periods, dict) else {})  # fmt: skip
        d1, ytd = _rets(snap, "1D"), _rets(snap, "YTD")
        sub = gics.sub_industry(sym)
        by_cap = sorted((s for s in gics.members if s != sym),
                        key=lambda s: -(caps.get(s) or 0))  # fmt: skip
        same = [s for s in by_cap if sub and gics.sub_industry(s) == sub]
        rest = [s for s in by_cap if gics.sector(s) == sector and s not in same]
        out |= {
            "name": gics.name(sym),
            "sector": SECTOR_NAME.get(sector or "", sector),
            "sub_industry": sub,
            "sector_etf": etf,
            "sector_periods": periods,
            "sector_as_of": snap.get("as_of") if periods else None,
            "peers": [
                PeerOut(
                    symbol=s,
                    name=gics.name(s),
                    market_cap=caps.get(s),
                    d1=d1.get(s),
                    ytd=ytd.get(s),
                )
                for s in [*same, *rest][:PEERS]
            ],
        }
    raw = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else None
    if raw is not None:
        metrics = []
        for key, label, kind in METRICS:
            v = raw.get(key)
            v = _num(v)
            if v is not None and kind == "pct":
                v /= 100
            metrics.append(MetricOut(key=key, label=label, kind=kind, value=v))
        out["metrics"] = metrics
        out["metrics_updated"] = doc.get("metrics_updated")
    news = []
    for n in doc.get("news") or []:
        try:
            news.append(NewsItemOut(**n))
        except (TypeError, ValidationError):
            continue  # 单条格式不符只跳过这一条
    out["news"] = news
    out["news_updated"] = doc.get("news_updated")
    try:
        return StockExtras(**out)
    except ValidationError as e:
        log.warning("%s 个股文件格式不符：%s", sym, type(e).__name__)
        return StockExtras(**{**out, "news_updated": None, "metrics_updated": None})
