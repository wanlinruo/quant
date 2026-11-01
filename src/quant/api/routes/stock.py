"""个股页：行情摘要、技术面与“我的系统”中的关系（持仓、排名、成交按账户 mode）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from quant.api import stock
from quant.api.deps import get_constituents, get_events, get_session, get_store, get_universe_store
from quant.api.portfolio import Stores
from quant.api.schemas import Mode, StockProfile
from quant.core.calendar import get_calendar

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/stock/{symbol}/profile")
def profile(symbol: str, mode: Mode, s: SessionDep) -> StockProfile:
    stores = Stores(get_store(), get_universe_store())
    return stock.profile(
        s, mode, symbol.upper(), stores, get_events(), get_constituents(), get_calendar()
    )
