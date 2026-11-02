"""行情：数据状态与单个标的日线（读 Parquet，不需要数据库）。"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from quant.api import intraday_view, queries, stock
from quant.api.deps import get_engine, get_settings, get_store, get_universe_store
from quant.api.portfolio import Stores
from quant.api.schemas import (
    BarsOut,
    DataStatusRow,
    IntradayOut,
    MarketCalendar,
    MarketNews,
    MarketOverview,
    NewsRefOut,
    TickerItemOut,
)
from quant.core.calendar import get_calendar
from quant.data.market_store import calendar_path, macro_path, news_path, snapshot_path

router = APIRouter()
log = logging.getLogger("quant.api.market")

DATA_STATUS_TTL = 300  # 秒；全量校验所有标的较慢，与改版前的面板一样缓存 5 分钟
_status_cache: dict[tuple[str, date], tuple[float, list[DataStatusRow]]] = {}


def _now() -> float:
    return time.monotonic()


@router.get("/market/intraday")
def market_intraday() -> IntradayOut:
    """盘中行情（#93）：常规时段返回今天的最新报价；其它时段只返回所处时段与说明。"""
    eng, cal = get_engine(), get_calendar()
    if eng is None:
        return intraday_view.intraday(None, cal)
    try:
        with Session(eng) as s:
            return intraday_view.intraday(s, cal)
    except OperationalError:  # 数据库连不上：只返回时段与说明，不影响首页其它区块
        return intraday_view.intraday(None, cal)


@router.get("/market/data-status")
def data_status() -> list[DataStatusRow]:
    cal = get_calendar()
    store = get_store()
    expected = cal.last_completed_trading_day(datetime.now(UTC))
    key = (str(store.root), expected)
    hit = _status_cache.get(key)
    if hit is not None and _now() - hit[0] < DATA_STATUS_TTL:
        return hit[1]
    rows = queries.data_status(store, cal, expected)
    _status_cache.clear()
    _status_cache[key] = (_now(), rows)
    return rows


@router.get("/market/bars/{symbol}")
def bars(symbol: str, start: date | None = None) -> BarsOut:
    return stock.bars(Stores(get_store(), get_universe_store()), symbol.upper(), start)


def _load(path: Path) -> Any:
    """读任务生成的 JSON；没有或损坏时为 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("%s 无法读取：%s", path.name, type(e).__name__)
        return None


def _macro(data_dir: Path) -> list[TickerItemOut]:
    doc = _load(macro_path(data_dir))
    try:
        return [TickerItemOut(**i) for i in doc["items"]] if isinstance(doc, dict) else []
    except (KeyError, TypeError, ValidationError):
        return []


def _reasons(data_dir: Path, as_of: Any) -> dict[str, NewsRefOut]:
    doc = _load(news_path(data_dir))
    if not isinstance(doc, dict) or as_of is None or doc.get("as_of") != str(as_of):
        return {}
    try:
        return {k: NewsRefOut(**v) for k, v in (doc.get("reasons") or {}).items()}
    except (AttributeError, TypeError, ValidationError):
        return {}


@router.get("/market/overview")
def overview() -> MarketOverview:
    """首页看板：只读 market_data 快照与 market_extras 的宏观、新闻文件；缺失或损坏为 pending。"""
    data_dir = get_settings().data_dir
    macro = _macro(data_dir)
    doc = _load(snapshot_path(data_dir))
    if not isinstance(doc, dict):
        return MarketOverview(status="pending", macro=macro)
    try:
        out = MarketOverview(status="ok", **doc)
    except (TypeError, ValidationError) as e:
        log.warning("首页快照无法读取：%s", type(e).__name__)
        return MarketOverview(status="pending", macro=macro)
    reasons = _reasons(data_dir, out.as_of)
    movers = [m.model_copy(update={"reason": reasons.get(m.symbol)}) for m in out.movers]
    return out.model_copy(update={"macro": macro, "movers": movers})


@router.get("/market/news")
def news() -> MarketNews:
    doc = _load(news_path(get_settings().data_dir))
    try:
        return MarketNews(status="ok", generated_at=doc["generated_at"], items=doc["items"])
    except (KeyError, TypeError, ValidationError):
        return MarketNews(status="pending")


@router.get("/market/calendar")
def calendar() -> MarketCalendar:
    doc = _load(calendar_path(get_settings().data_dir))
    try:
        return MarketCalendar(status="ok", generated_at=doc["generated_at"],
                              released=doc["released"], upcoming=doc["upcoming"])  # fmt: skip
    except (KeyError, TypeError, ValidationError):
        return MarketCalendar(status="pending")
