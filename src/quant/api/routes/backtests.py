"""回测：列表、净值、表单默认值，以及面板唯一的写操作——提交回测。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant.api import queries
from quant.api.backtest_view import analysis, positions_view, trades_view
from quant.api.backtests import submit
from quant.api.deps import get_session, get_settings
from quant.api.schemas import (
    BacktestAnalysis,
    BacktestDataStatus,
    BacktestDefaults,
    BacktestPositions,
    BacktestRunOut,
    BacktestSubmit,
    BacktestTrades,
    EquityPoint,
    Progress,
    QuotaOut,
    SubmitResult,
)
from quant.core.calendar import get_calendar
from quant.data import backtest_data as bd
from quant.data.quota import QuotaLedger
from quant.data.universe import constituents_path, load_constituents
from quant.strategies import ETF_OPTIONS, STRATEGIES
from quant.strategies.etf_momentum import MomentumParams

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
MAX_COMPARE = 5


@router.get("/backtests")
def list_runs(s: SessionDep) -> list[BacktestRunOut]:
    return queries.backtest_runs(s)


@router.get("/backtests/equity")
def equity(
    s: SessionDep, ids: Annotated[list[int], Query(max_length=MAX_COMPARE)]
) -> list[EquityPoint]:
    return queries.backtest_equity(s, ids)


def _data_status() -> BacktestDataStatus:
    """服务器回测数据的补齐进度（只读本地状态文件，不联网）。"""
    settings = get_settings()
    if settings.data.backtest_source != "tiingo":
        return BacktestDataStatus(source="yfinance")
    data_dir = settings.data_dir
    cal = get_calendar()
    path = constituents_path(data_dir)
    universe = load_constituents(path) if path.exists() else None
    wanted = bd.wanted_symbols(universe, cal.last_completed_trading_day(datetime.now(UTC)))
    lim = settings.tiingo
    st = bd.status(wanted, bd.BackfillState.load(bd.state_path(data_dir)),
                   QuotaLedger(bd.quota_path(data_dir), lim), data_dir)  # fmt: skip
    q = st.quota
    return BacktestDataStatus(
        source="tiingo", data_end=st.data_end,
        fixed=Progress(done=st.fixed_done, total=st.fixed_total),
        current=Progress(done=st.current_done, total=st.current_total),
        delisted=Progress(done=st.delisted_done, total=st.delisted_total),
        unavailable=st.unavailable,
        quota=QuotaOut(hour_used=q.hour_used, hour_limit=lim.hourly, day_used=q.day_used,
                       day_limit=lim.daily, month=q.month, month_symbols=q.month_symbols,
                       month_limit=lim.monthly_symbols),
        earnings=st.earnings,
    )  # fmt: skip


@router.get("/backtests/{run_id}/analysis")
def run_analysis(run_id: int, s: SessionDep) -> BacktestAnalysis:
    """回测页：统计条、主图、概览、收益分析、回撤与风险、参数与日志（请求时计算，只读）。"""
    out = analysis(s, run_id, get_settings())
    if out is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    return out


@router.get("/backtests/{run_id}/trades")
def run_trades(run_id: int, s: SessionDep) -> BacktestTrades:
    """交易明细：汇总、逐笔、按平仓原因、按股票（只读）。"""
    out = trades_view(s, run_id)
    if out is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    return out


@router.get("/backtests/{run_id}/positions")
def run_positions(
    run_id: int, s: SessionDep, day: Annotated[date | None, Query(alias="date")] = None
) -> BacktestPositions:
    """持仓变化：每日子仓 / 现金占比、择时减仓期、某日持仓、持有最长（只读）。"""
    out = positions_view(s, run_id, day)
    if out is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    return out


@router.get("/backtests/data-status")
def data_status() -> BacktestDataStatus:
    return _data_status()


@router.get("/backtests/defaults")
def defaults() -> BacktestDefaults:
    cfg = get_settings().costs
    d = MomentumParams()
    last = get_calendar().last_completed_trading_day(datetime.now(UTC))
    data_end = _data_status().data_end
    end = min(last, data_end) if data_end else last
    return BacktestDefaults(
        strategies=sorted(STRATEGIES),
        universe_options=list(ETF_OPTIONS),
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
        start=max(date(end.year - 10, end.month, 1), bd.MIN_BACKTEST_START)
        if data_end
        else date(end.year - 10, end.month, 1),
        min_start=bd.MIN_BACKTEST_START if _data_status().source == "tiingo" else None,
        end=end,
        data_end=data_end,
    )


@router.post("/backtests", status_code=201)
def submit_backtest(req: BacktestSubmit, s: SessionDep) -> SubmitResult:
    settings = get_settings()
    last = get_calendar().last_completed_trading_day(datetime.now(UTC))
    status = _data_status()
    if status.source == "tiingo" and status.data_end is None:
        raise HTTPException(status_code=422, detail=["回测数据尚未补齐固定标的，请稍后再提交"])
    configured = settings.strategy.params if settings.strategy.name == req.strategy else {}
    with s.begin():
        min_start = bd.MIN_BACKTEST_START if status.source == "tiingo" else None
        run_id = submit(s, req, settings.costs, last, data_end=status.data_end,
                        strategy_params=configured, min_start=min_start)  # fmt: skip
    return SubmitResult(id=run_id)
