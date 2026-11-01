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
    mode: Mode
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
    data_end: date | None = None  # 服务器回测数据截止日（tiingo 模式）
    min_start: date | None = None  # 服务器回测最早开始日（tiingo 模式）


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
    qty: int
    weight_from: float
    weight_to: float
    reason: str


class RebalancePreview(BaseModel):
    signal_date: date | None
    rows: list[PreviewRow]
    turnover: float
    est_commission: float | None
    missing_prices: list[str] = []  # 缺价格无法预估的代码


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


# ---- 个股页（#65）----


class BarsOut(BaseModel):
    adjusted: bool  # False：来自候选池目录（新浪，未复权）
    bars: list[Bar]


class QuoteOut(BaseModel):
    close: float
    change: float | None
    change_pct: float | None
    open: float
    high: float
    low: float
    volume: float
    turnover: float
    high_52w: float
    low_52w: float
    ret_1m: float | None
    ytd: float | None


class RelationOut(BaseModel):
    held: bool
    weight: float | None
    sleeve: Sleeve | None
    momentum_rank: int | None
    universe_size: int | None
    in_sp500: bool
    swing_universe: bool
    prev_holding: str | None
    next_holding: str | None


class KeyStatsOut(BaseModel):
    volume_ratio: float | None
    beta_1y: float | None
    vol_20d: float | None
    from_high: float | None
    to_ma120: float | None
    next_earnings: date | None
    days_to_earnings: int | None


class TechnicalsOut(BaseModel):
    rsi14: float | None
    atr14_pct: float | None
    rs_20d: float | None
    rs_60d: float | None
    ma10: float | None
    ma20: float | None
    ma120: float | None
    dist_ma10: float | None
    dist_ma20: float | None
    dist_ma120: float | None


class SwingCheckOut(BaseModel):
    trend_up: bool | None
    above_support: bool | None
    dist_exit: float | None


class RankPoint(BaseModel):
    date: date
    rank: int | None


class EarningsMove(BaseModel):
    date: date
    move: float | None


class EarningsOut(BaseModel):
    next: date | None
    held_through: bool
    history: list[EarningsMove]


class PeriodOut(BaseModel):
    start: date
    end: date


class StockProfile(BaseModel):
    symbol: str
    as_of: date | None
    adjusted: bool
    quote: QuoteOut | None
    relation: RelationOut
    periods: dict[str, float | None]
    periods_qqq: dict[str, float | None]
    key_stats: KeyStatsOut
    technicals: TechnicalsOut
    swing: SwingCheckOut
    rank_history: list[RankPoint]
    earnings: EarningsOut
    holding_periods: list[PeriodOut]
    trades: list[FillOut]


# ---- 服务器回测数据（#66）----


class Progress(BaseModel):
    done: int
    total: int


class QuotaOut(BaseModel):
    hour_used: int
    hour_limit: int
    day_used: int
    day_limit: int
    month: str
    month_symbols: int
    month_limit: int


class BacktestDataStatus(BaseModel):
    source: Literal["yfinance", "tiingo"]
    data_end: date | None = None
    fixed: Progress | None = None
    current: Progress | None = None
    delisted: Progress | None = None
    unavailable: list[str] = []
    quota: QuotaOut | None = None
    earnings: Literal["complete", "partial", "none"] | None = None


# ---- 首页市场看板（#70）----


class TickerItemOut(BaseModel):
    symbol: str
    label: str
    close: float | None
    change: float | None
    change_pct: float | None
    spark: list[float]


class StatOut(BaseModel):
    value: float | None
    prev: float | None


class BreadthOut(BaseModel):
    advancers: StatOut
    decliners: StatOut
    unchanged: StatOut
    new_highs: StatOut
    new_lows: StatOut
    above_50: StatOut
    above_200: StatOut
    up_volume_ratio: StatOut


class StylePairOut(BaseModel):
    key: str
    label: str
    long: str
    short: str
    d1: float | None
    m1: float | None


class SectorRowOut(BaseModel):
    symbol: str
    name: str
    d1: float | None
    w1: float | None
    m1: float | None
    ytd: float | None
    rel_spy_d1: float | None
    spark: list[float]


class MoverOut(BaseModel):
    symbol: str
    name: str | None
    sector: str | None
    market_cap: float | None
    close: float
    change_pct: float
    volume_ratio: float | None
    signals: list[str]
    spark: list[float]
    held: bool = False


class HeatCellOut(BaseModel):
    symbol: str
    name: str | None
    sector: str | None
    market_cap: float
    ret: float | None


class CandleOut(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketOverview(BaseModel):
    status: Literal["ok", "pending"]
    as_of: date | None = None
    stale: bool = False
    generated_at: datetime | None = None
    members: int = 0
    coverage: float | None = None
    strip: list[TickerItemOut] = []
    highlights: str = ""
    breadth: BreadthOut | None = None
    styles: list[StylePairOut] = []
    sectors: list[SectorRowOut] = []
    movers: list[MoverOut] = []
    heatmap: dict[str, list[HeatCellOut]] = {}
    index_candles: dict[str, list[CandleOut]] = {}
