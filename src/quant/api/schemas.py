"""面板接口的数据模型。前端类型由这里生成（python -m quant.api.openapi）。

不得包含数据库地址、密钥、账户号等字段。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

Mode = Literal["sim", "paper", "live"]
MODES: tuple[Mode, ...] = ("sim", "paper", "live")
MODE_LABELS: dict[Mode, str] = {"sim": "本地模拟", "paper": "模拟盘", "live": "实盘"}


class AccountInfo(BaseModel):
    mode: Mode
    label: str
    has_data: bool


class Meta(BaseModel):
    trading_mode: Mode
    broker: str
    database_configured: bool
    accounts: list[AccountInfo]  # 固定按 sim、paper、live 顺序
    default_account: Mode
    data_as_of: datetime | None


class Position(BaseModel):
    snapshot_date: date
    symbol: str
    qty: int
    avg_cost: float
    market_value: float


class AccountPoint(BaseModel):
    date: date
    cash: float
    net_liquidation: float


class SignalOut(BaseModel):
    mode: Mode
    strategy: str
    signal_date: date
    data_as_of: date
    target_weights: dict[str, float]
    detail: dict[str, Any] | None = None


class OrderOut(BaseModel):
    created_at: datetime
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    qty: int
    limit_price: float | None
    reference_price: float | None
    status: str
    filled_qty: int
    avg_fill_price: float | None
    reason: str


class FillOut(BaseModel):
    filled_at: datetime
    symbol: str
    side: str
    qty: int
    price: float
    commission: float


class BacktestRunOut(BaseModel):
    id: int
    strategy: str
    status: str
    start: date
    end: date
    params: dict[str, Any]
    costs: dict[str, str] | None
    metrics: dict[str, Any]
    error: str
    created_at: datetime
    finished_at: datetime | None


class EquityPoint(BaseModel):
    run_id: int
    date: date
    equity: float
    drawdown: float


class JobStatusOut(BaseModel):
    job_name: str
    trade_date: date
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str


class FailureOut(BaseModel):
    time: datetime
    source: str
    message: str


class QueueStatus(BaseModel):
    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0


class DataStatusRow(BaseModel):
    symbol: str
    last_date: date | None
    rows: int
    sources: str
    last_ingested: datetime | None
    ok: bool
    issues: str


class Bar(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: float


class CostsIn(BaseModel):
    commission_per_share: Decimal | None
    min_commission: Decimal | None
    slippage: Decimal


class BacktestSubmit(BaseModel):
    strategy: str
    start: date
    end: date
    universe: list[str]
    safe_asset: str
    lookback: int
    top_k: int
    sma_window: int
    cash: Decimal
    costs: CostsIn


class SubmitResult(BaseModel):
    id: int


class BacktestDefaults(BaseModel):
    """提交表单的默认值（与改版前的面板表单一致）。"""

    strategies: list[str]
    universe_options: list[str]
    universe: list[str]
    safe_asset: str
    lookback: int
    top_k: int
    sma_window: int
    cash: Decimal
    commission_per_share: str | None
    min_commission: str | None
    slippage: str
    start: date
    end: date


# ---- 组合页（#65）----

Sleeve = Literal["momentum", "swing", "both", "exit"]
SignalState = Literal["ok", "near", "exit"]


class PortfolioSummaryOut(BaseModel):
    today_pnl: float | None
    today_return: float | None
    total_return: float | None
    qqq_return: float | None
    spy_return: float | None
    max_drawdown: float | None
    current_drawdown: float | None
    ann_vol: float | None
    sharpe: float | None


class RiskOut(BaseModel):
    beta_spy: float | None
    corr_qqq: float | None
    var95_1d: float | None


class TimingOut(BaseModel):
    factor: float
    vol_symbol: str
    vol_20d: float | None
    threshold: float


class SleeveAlloc(BaseModel):
    sleeve: Literal["momentum", "swing", "cash", "other"]
    actual: float
    target: float | None


class ReconcileOut(BaseModel):
    trading_enabled: bool
    last_report_status: str | None


class PortfolioOverview(BaseModel):
    as_of: date | None
    net_liquidation: float | None
    cash: float | None
    summary: PortfolioSummaryOut
    risk: RiskOut
    top5: float | None
    max_single: float | None
    exposure: float | None
    timing: TimingOut | None
    allocation: list[SleeveAlloc]
    earnings_next_week: list[str]
    reconcile: ReconcileOut


class PerfPoint(BaseModel):
    date: date
    nav: float
    portfolio: float
    qqq: float | None
    spy: float | None
    drawdown: float


class PerformanceOut(BaseModel):
    points: list[PerfPoint]
    rebalance_dates: list[date]


class HoldingRow(BaseModel):
    symbol: str
    sleeve: Sleeve | None
    qty: int
    avg_cost: float
    price: float | None
    change_pct: float | None
    market_value: float
    weight: float | None
    target_weight: float
    deviation: float | None
    unrealized_pnl: float | None
    state: SignalState | None
    state_note: str
    next_earnings: date | None
    holding_days: int


class ContributionRow(BaseModel):
    symbol: str
    sleeve: Sleeve | None
    pnl: float


class CalendarDayOut(BaseModel):
    date: date
    pnl: float | None
    ret: float | None


class PreviewRow(BaseModel):
    side: Literal["BUY", "SELL"]
    symbol: str
    sleeve: Sleeve | None
    weight_from: float
    weight_to: float
    reason: str


class RebalancePreview(BaseModel):
    signal_date: date | None
    rows: list[PreviewRow]
    turnover: float
    est_commission: float | None


class ClosedTradeOut(BaseModel):
    symbol: str
    open_date: date
    close_date: date
    qty: int
    open_price: float
    close_price: float
    pnl: float
    ret: float
    days: int


class TradeStatsOut(BaseModel):
    count: int
    win_rate: float | None
    avg_pnl: float | None
    avg_slippage: float | None


class ClosedTradesOut(BaseModel):
    trades: list[ClosedTradeOut]
    stats: TradeStatsOut
