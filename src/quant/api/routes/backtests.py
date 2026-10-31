"""回测：列表、净值、表单默认值，以及面板唯一的写操作——提交回测。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant.api import queries
from quant.api.backtests import submit
from quant.api.deps import get_session, get_settings
from quant.api.schemas import (
    BacktestDefaults,
    BacktestRunOut,
    BacktestSubmit,
    EquityPoint,
    SubmitResult,
)
from quant.core.calendar import get_calendar
from quant.strategies import STRATEGIES
from quant.strategies.etf_momentum import MomentumParams

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
UNIVERSE_OPTIONS = ["SPY", "QQQ", "TLT", "GLD", "IWM", "EFA", "EEM", "VNQ", "DBC"]
MAX_COMPARE = 5


@router.get("/backtests")
def list_runs(s: SessionDep) -> list[BacktestRunOut]:
    return queries.backtest_runs(s)


@router.get("/backtests/equity")
def equity(
    s: SessionDep, ids: Annotated[list[int], Query(max_length=MAX_COMPARE)]
) -> list[EquityPoint]:
    return queries.backtest_equity(s, ids)


@router.get("/backtests/defaults")
def defaults() -> BacktestDefaults:
    cfg = get_settings().costs
    d = MomentumParams()
    last = get_calendar().last_completed_trading_day(datetime.now(UTC))
    return BacktestDefaults(
        strategies=sorted(STRATEGIES),
        universe_options=UNIVERSE_OPTIONS,
        universe=list(d.universe),
        safe_asset=d.safe_asset,
        lookback=d.lookback,
        top_k=d.top_k,
        sma_window=d.sma_window,
        cash=Decimal(100_000),
        commission_per_share=None
        if cfg.commission_per_share is None
        else str(cfg.commission_per_share),
        min_commission=None if cfg.min_commission is None else str(cfg.min_commission),
        slippage=str(cfg.slippage),
        start=date(last.year - 10, last.month, 1),
        end=last,
    )


@router.post("/backtests", status_code=201)
def submit_backtest(req: BacktestSubmit, s: SessionDep) -> SubmitResult:
    with s.begin():
        run_id = submit(s, req, get_settings().costs)
    return SubmitResult(id=run_id)
