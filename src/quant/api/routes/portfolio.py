"""账户（组合）数据：按账户模式 sim / paper / live 查询，只读。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant.api import queries
from quant.api.deps import get_session
from quant.api.schemas import AccountPoint, FillOut, Mode, OrderOut, Position, SignalOut

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=1000)]


@router.get("/portfolio/{mode}/positions")
def positions(mode: Mode, s: SessionDep) -> list[Position]:
    return queries.positions(s, mode)


@router.get("/portfolio/{mode}/curve")
def curve(mode: Mode, s: SessionDep) -> list[AccountPoint]:
    return queries.account_curve(s, mode)


@router.get("/portfolio/{mode}/orders")
def orders(mode: Mode, s: SessionDep, limit: Limit = 200) -> list[OrderOut]:
    return queries.orders(s, mode, limit)


@router.get("/portfolio/{mode}/fills")
def fills(mode: Mode, s: SessionDep, limit: Limit = 200) -> list[FillOut]:
    return queries.fills(s, mode, limit)


@router.get("/signals/latest")
def latest_signals(s: SessionDep) -> list[SignalOut]:
    return queries.latest_signals(s)
