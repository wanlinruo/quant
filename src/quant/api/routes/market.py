"""行情：数据状态与单个标的日线（读 Parquet，不需要数据库）。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter

from quant.api import queries
from quant.api.deps import get_store
from quant.api.schemas import Bar, DataStatusRow
from quant.core.calendar import get_calendar

router = APIRouter()


@router.get("/market/data-status")
def data_status() -> list[DataStatusRow]:
    cal = get_calendar()
    return queries.data_status(get_store(), cal, cal.last_completed_trading_day(datetime.now(UTC)))


@router.get("/market/bars/{symbol}")
def bars(symbol: str, start: date | None = None) -> list[Bar]:
    return queries.bars(get_store(), symbol.upper(), start)
