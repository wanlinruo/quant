"""行情：数据状态与单个标的日线（读 Parquet，不需要数据库）。"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

from fastapi import APIRouter

from quant.api import queries
from quant.api.deps import get_store
from quant.api.schemas import Bar, DataStatusRow
from quant.core.calendar import get_calendar

router = APIRouter()

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
def bars(symbol: str, start: date | None = None) -> list[Bar]:
    return queries.bars(get_store(), symbol.upper(), start)
