"""行情：数据状态与单个标的日线（读 Parquet，不需要数据库）。"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime

from fastapi import APIRouter

from quant.api import queries, stock
from quant.api.deps import get_settings, get_store, get_universe_store
from quant.api.portfolio import Stores
from quant.api.schemas import BarsOut, DataStatusRow, MarketOverview
from quant.core.calendar import get_calendar
from quant.data.market_store import snapshot_path

router = APIRouter()
log = logging.getLogger("quant.api.market")

DATA_STATUS_TTL = 300  # 秒；全量校验所有标的较慢，与改版前的面板一样缓存 5 分钟
_status_cache: dict[tuple[str, date], tuple[float, list[DataStatusRow]]] = {}


def _now() -> float:
    return time.monotonic()


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


@router.get("/market/overview")
def overview() -> MarketOverview:
    """首页市场看板：只读 market_data 任务生成的快照；没有或损坏时为 pending。"""
    path = snapshot_path(get_settings().data_dir)
    if not path.exists():
        return MarketOverview(status="pending")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return MarketOverview(status="ok", **doc)
    except (ValueError, TypeError) as e:
        log.warning("首页快照无法读取：%s", type(e).__name__)
        return MarketOverview(status="pending")
