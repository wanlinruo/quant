"""账户（组合）数据：按账户模式 sim / paper / live 查询，只读。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant.api import portfolio, queries
from quant.api.deps import get_events, get_session, get_settings, get_store, get_universe_store
from quant.api.schemas import (
    AccountPoint,
    CalendarDayOut,
    ClosedTradesOut,
    ContributionRow,
    FillOut,
    HoldingRow,
    Mode,
    OrderOut,
    PerformanceOut,
    PortfolioOverview,
    Position,
    RebalancePreview,
    SignalOut,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Month = Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


def _stores() -> portfolio.Stores:
    return portfolio.Stores(get_store(), get_universe_store())


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
def latest_signals(s: SessionDep, mode: Mode) -> list[SignalOut]:
    return queries.latest_signals(s, mode)


@router.get("/portfolio/{mode}/overview")
def overview(mode: Mode, s: SessionDep) -> PortfolioOverview:
    return portfolio.overview(s, mode, _stores(), get_events())


@router.get("/portfolio/{mode}/performance")
def performance(mode: Mode, s: SessionDep) -> PerformanceOut:
    return portfolio.performance(s, mode, _stores())


@router.get("/portfolio/{mode}/holdings")
def holdings(mode: Mode, s: SessionDep) -> list[HoldingRow]:
    return portfolio.holdings(s, mode, _stores(), get_events())


@router.get("/portfolio/{mode}/contributions")
def contributions(
    mode: Mode, s: SessionDep, period: Literal["day", "month"] = "day"
) -> list[ContributionRow]:
    return portfolio.contributions(s, mode, _stores(), period)


@router.get("/portfolio/{mode}/calendar")
def calendar(mode: Mode, s: SessionDep, month: Month = None) -> list[CalendarDayOut]:
    return portfolio.calendar(s, mode, month)


@router.get("/portfolio/{mode}/rebalance-preview")
def rebalance_preview(mode: Mode, s: SessionDep) -> RebalancePreview:
    cfg = get_settings()
    return portfolio.rebalance_preview(s, mode, _stores(), cfg.costs, cfg.executor)


@router.get("/portfolio/{mode}/closed-trades")
def closed_trades(mode: Mode, s: SessionDep) -> ClosedTradesOut:
    return portfolio.closed_trades(s, mode)
