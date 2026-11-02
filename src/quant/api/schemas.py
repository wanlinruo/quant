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


class RobustProgressOut(BaseModel):
    total: int
    done: int
    failed: int
    queued: int
    running: int


class RbMetricsOut(BaseModel):
    cagr: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None


class RbRowOut(BaseModel):
    label: str
    run_id: int | None = None
    status: str
    metrics: RbMetricsOut | None = None
    passed: bool | None = None  # 只有组合策略的相邻设置判定
    base: bool = False  # 本次（父回测）
    error: str | None = None  # 失败原因第一行


class RbCellOut(BaseModel):
    x: int
    y: int
    label: str
    run_id: int | None = None
    status: str  # done / queued / running / failed / skipped（参数越界未跑）
    metrics: RbMetricsOut | None = None
    center: bool = False
    error: str | None = None  # 失败原因第一行


class RbGridOut(BaseModel):
    x_label: str
    y_label: str
    x_values: list[str]
    y_values: list[str]
    cells: list[RbCellOut]


class RbWfSegOut(BaseModel):
    year: int
    chosen: str
    test_return: float
    base_return: float


class RbWfOut(BaseModel):
    train_years: int
    note: str = ""
    segments: list[RbWfSegOut] = []
    strategy: RbMetricsOut | None = None  # 拼接的样本外
    base: RbMetricsOut | None = None  # 本次参数同期


class RobustnessOut(BaseModel):
    status: Literal["not_requested", "pending", "done"]
    note: str = ""
    progress: RobustProgressOut | None = None
    grid: RbGridOut | None = None
    neighbors: list[RbRowOut] = []
    neighbors_verdict: bool | None = None
    neighbors_note: str = ""
    walk_forward: RbWfOut | None = None
    costs: list[RbRowOut] = []
    starts: list[RbRowOut] = []


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
    robust: RobustProgressOut | None = None  # 只有勾选了稳健性检验的父回测有


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
    # 非表单策略（如 combo）的参数覆盖：只含用户改过的键；嵌套组（momentum / swing）只含改过的子键
    params: dict[str, Any] | None = None
    robustness: bool = False  # 同时跑稳健性检验（#68）


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
    as_of: date | None = None  # 宏观项自己的数据日期（FRED 次日才更新）
    note: str = ""  # 口径说明，如“广义美元指数 · 周”
    unit: Literal["price", "yield"] = "price"  # 收益率的涨跌按基点显示


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


class NewsRefOut(BaseModel):
    headline: str
    source: str
    time: datetime
    url: str


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
    reason: NewsRefOut | None = None  # 关联新闻（market_extras 生成，与快照同一天才合并）


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
    volume: float | None = None  # 盘中当日 K 线没有成交量（#93）


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
    macro: list[TickerItemOut] = []  # VIX、美债、美元、原油、比特币（market_extras）
    sector_periods: dict[str, dict[str, float | None]] = {}  # 板块 ETF 区间收益（个股页）
    # 盘中快照（#93）：最后一天为盘中价格，约每 10 分钟更新；成交量相关指标收盘后更新
    intraday: bool = False
    intraday_time: datetime | None = None


class NewsItemOut(BaseModel):
    time: datetime
    headline: str
    source: str
    url: str
    tags: list[str]
    category: Literal["macro", "company", "earnings"]


class MarketNews(BaseModel):
    status: Literal["ok", "pending"]
    generated_at: datetime | None = None
    items: list[NewsItemOut] = []


class CalItemOut(BaseModel):
    time: datetime
    date_only: bool
    kind: Literal["econ", "earnings", "fomc"]
    title: str
    impact: int
    actual: str | None
    forecast: str | None
    previous: str | None
    symbols: list[str]
    held: bool
    session: str | None


class MarketCalendar(BaseModel):
    status: Literal["ok", "pending"]
    generated_at: datetime | None = None
    released: list[CalItemOut] = []
    upcoming: list[CalItemOut] = []


# ---- 个股页估值、新闻、同板块（#70 第 3 批）----


class MetricOut(BaseModel):
    key: str
    label: str
    kind: Literal["ratio", "pct"]
    value: float | None  # pct 为小数（0.012 即 1.2%）


class PeerOut(BaseModel):
    symbol: str
    name: str | None
    market_cap: float | None
    d1: float | None
    ytd: float | None


class StockExtras(BaseModel):
    symbol: str
    covered: bool  # 是否在“标普 500 成分 ∪ 持仓”的覆盖范围内
    in_sp500: bool = False  # 标普 500 成分（才有板块与同板块）
    name: str | None = None
    sector: str | None = None
    sub_industry: str | None = None
    market_cap: float | None = None
    sector_etf: str | None = None
    sector_periods: dict[str, float | None] = {}
    peers: list[PeerOut] = []
    metrics: list[MetricOut] = []
    metrics_updated: date | None = None
    news: list[NewsItemOut] = []
    news_updated: datetime | None = None


# ---- 回测分析（#67 A 批）----


class BtStatOut(BaseModel):
    key: str
    label: str
    value: float | None
    qqq: float | None
    kind: Literal["pct", "ratio", "int"]


class BtSeriesOut(BaseModel):
    """净值归一化到 1；qqq / spy 缺数据时为 None，个别日期缺失为 None。"""

    dates: list[date]
    strategy: list[float]
    qqq: list[float | None] | None
    spy: list[float | None] | None
    drawdown: list[float]
    qqq_drawdown: list[float | None] | None


class BtCheckOut(BaseModel):
    label: str
    passed: bool | None  # None：数据不足或需稳健性检验（#68）


class BtYearOut(BaseModel):
    year: int
    strategy: float | None
    qqq: float | None


class BtOverviewOut(BaseModel):
    checks: list[BtCheckOut]
    split: dict[str, dict[str, dict[str, Any] | None]]  # full / ins / oos → strategy / qqq → 指标
    yearly: list[BtYearOut]


class BtMonthRowOut(BaseModel):
    year: int
    months: list[float | None]
    year_total: float | None
    qqq_year: float | None


class BtBinOut(BaseModel):
    lo: float
    hi: float
    count: int


class BtBestWorstOut(BaseModel):
    period: str
    best: float | None
    best_at: str
    worst: float | None
    worst_at: str


class BtRollingOut(BaseModel):
    date: date
    ret: float | None
    sharpe: float | None


class BtReturnsOut(BaseModel):
    monthly: list[BtMonthRowOut]
    distribution: list[BtBinOut]
    best_worst: list[BtBestWorstOut]
    rolling: list[BtRollingOut]
    sleeves: BtSleevesOut | None = None  # 子仓累计收益贡献（B 批；旧回测为 None）


class BtEpisodeOut(BaseModel):
    start: date
    trough: date
    end: date | None
    depth: float | None
    down_days: int
    recovery_days: int | None
    qqq: float | None


class BtRowOut(BaseModel):
    label: str
    value: float | None
    qqq: float | None = None


class BtBetaOut(BaseModel):
    date: date
    beta: float | None


class BtRiskOut(BaseModel):
    episodes: list[BtEpisodeOut]
    capture: dict[str, float | None]
    table: list[BtRowOut]
    rolling_beta: list[BtBetaOut]
    tail: list[BtRowOut]
    timing: list[BtTimingOut] = []  # 择时减仓时段（B 批）


class BtParamOut(BaseModel):
    key: str
    value: Any
    default: Any
    changed: bool


class BtReproOut(BaseModel):
    source: str | None
    data_end: str | None
    coverage: Any
    command: str
    code_version: str | None = None  # 回测时的代码提交号（C 批起记录）
    log: list[str] = []  # 运行日志（quant.* INFO 及以上，最多约 200 行）


class BacktestAnalysis(BaseModel):
    id: int
    strategy: str
    status: str
    start: date
    end: date
    error: str | None
    initial_cash: float | None
    duration_s: float | None
    oos_start: date
    bench_note: str | None = None  # QQQ 数据不覆盖回测区间时的说明（对照指标为空）
    stats: list[BtStatOut] = []
    series: BtSeriesOut | None = None
    overview: BtOverviewOut | None = None
    returns: BtReturnsOut | None = None
    risk: BtRiskOut | None = None
    params: list[BtParamOut] = []
    repro: BtReproOut | None = None


# ---- 回测交易明细与持仓变化（#67 B 批）----


class BtTradeOut(BaseModel):
    seq: int
    symbol: str
    sleeve: str
    open_date: date
    open_price: float
    close_date: date | None
    close_price: float | None
    qty: float
    ret: float | None
    pnl: float
    exit_reason: str | None
    days: int  # 持有交易日数（未平仓算到回测最后一天）
    adjustments: list[dict[str, Any]]


class BtTradeRef(BaseModel):
    symbol: str
    pnl: float
    ret: float | None


class BtTradesSummary(BaseModel):
    trades: int
    closed: int
    win_rate: float | None
    profit_factor: float | None
    avg_days: float | None
    avg_ret: float | None
    best: BtTradeRef | None
    worst: BtTradeRef | None


class BtReasonOut(BaseModel):
    reason: str
    count: int
    win_rate: float | None
    avg_ret: float | None
    pnl: float


class BtSymbolOut(BaseModel):
    symbol: str
    trades: int
    pnl: float
    win_rate: float | None
    days: int


class BacktestTrades(BaseModel):
    note: str | None = None  # 没有交易记录时的说明
    summary: BtTradesSummary | None = None
    trades: list[BtTradeOut] = []
    by_reason: list[BtReasonOut] = []
    by_symbol: list[BtSymbolOut] = []


class BtDailyPosOut(BaseModel):
    date: date
    cash: float
    sleeves: dict[str, float]
    timing_factor: float | None


class BtHoldingOut(BaseModel):
    symbol: str
    sleeve: str
    weight: float


class BtLongestOut(BaseModel):
    symbol: str
    sleeve: str
    days: int
    open_date: date
    close_date: date | None


class BtTimingOut(BaseModel):
    start: date
    end: date
    days: int
    factor: float
    ret: float | None  # 该时段策略收益
    est_full: float | None  # 估算：系数为 1 时的收益（期间收益 ÷ 平均系数）
    saved: float | None  # 少亏（或少赚）= ret − est_full


class BacktestPositions(BaseModel):
    note: str | None = None
    daily: list[BtDailyPosOut] = []
    timing: list[BtTimingOut] = []
    day: date | None = None
    holdings: list[BtHoldingOut] = []
    longest: list[BtLongestOut] = []


class BtSleevesOut(BaseModel):
    dates: list[date]
    series: dict[str, list[float]]  # 子仓累计盈亏 ÷ 初始资金


# ---- 回测对比（#67 C 批）----


class BtCompareRun(BaseModel):
    id: int
    strategy: str
    start: date
    end: date


class BtCompareMetric(BaseModel):
    key: str
    label: str
    kind: Literal["pct", "ratio", "int"]
    scope: Literal["common", "full"]  # 共同区间重算 / 各自全区间（交易口径）
    values: dict[str, float | None]  # 回测编号 → 值
    best: int | None


class BtCompareParam(BaseModel):
    key: str
    values: dict[str, Any]
    differs: bool


class BacktestCompare(BaseModel):
    note: str | None = None
    start: date | None = None
    end: date | None = None
    skipped: list[int] = []
    runs: list[BtCompareRun] = []
    dates: list[date] = []
    series: dict[str, list[float | None]] = {}
    drawdown: dict[str, list[float | None]] = {}
    metrics: list[BtCompareMetric] = []
    params: list[BtCompareParam] = []
    corr: list[list[float | None]] = []


class ParamField(BaseModel):
    """新建回测表单的一个参数（由策略参数数据类自省；默认值已含服务器配置的覆盖）。"""

    key: str
    group: str | None  # combo 的子仓参数组：momentum / swing
    type: Literal["int", "float", "str", "bool", "list"]
    default: Any


# ---- 系统页（#71 A 批）----


class SysItemOut(BaseModel):
    level: Literal["ok", "warn", "error"]
    text: str
    command: str | None = None


class SysBannerOut(BaseModel):
    level: Literal["ok", "warn", "error"]
    items: list[SysItemOut] = []


class SysCardOut(BaseModel):
    key: str
    label: str
    value: str
    sub: str = ""
    level: Literal["ok", "warn", "error", "info"] = "info"


class SysCellOut(BaseModel):
    date: date
    status: Literal[
        "success", "retry_success", "failed", "running", "missing", "pending", "blocked", "na"
    ]
    attempts: int = 0
    duration_s: float | None = None


class SysJobRowOut(BaseModel):
    job: str
    cells: list[SysCellOut]
    success_rate: float | None
    avg_s: float | None


class SysGridOut(BaseModel):
    dates: list[date]
    jobs: list[SysJobRowOut]


class SysTodayOut(BaseModel):
    job: str
    planned_at: datetime
    status: str
    duration_s: float | None = None
    error: str | None = None


class SysSourceOut(BaseModel):
    key: str
    label: str
    status: Literal["ok", "stale", "pending", "warn"]
    updated: datetime | None = None
    detail: str = ""
    progress: float | None = None


class SysRiskOut(BaseModel):
    label: str
    used: float | None = None
    limit: float | None = None
    kind: Literal["pct", "usd", "int", "text"] = "pct"
    text: str = ""


class SysDayOut(BaseModel):
    date: date
    weekday: str
    tags: list[str] = []


class SysEventOut(BaseModel):
    time: datetime
    level: Literal["info", "warn", "error"]
    text: str


class SysQueueOut(BaseModel):
    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    current_id: int | None = None
    current_strategy: str | None = None
    current_started: datetime | None = None
    last_finished: datetime | None = None


class SysResourceOut(BaseModel):
    key: str
    label: str
    value: float | None = None  # 百分比
    text: str = ""
    level: Literal["ok", "warn", "error", "info"] = "info"
    series: list[float | None] = []  # 24 小时，每 5 分钟均值


class SysContainerOut(BaseModel):
    name: str
    state: str | None = None
    status: str | None = None
    cpu_pct: float | None = None
    mem_bytes: int | None = None
    mem_limit: int | None = None
    started_at: str | None = None
    restarts: int | None = None
    stale: bool = False  # 超过 5 分钟没有新采样


class SysTrendOut(BaseModel):
    times: list[datetime] = []  # 7 天，每小时
    cpu: list[float | None] = []
    mem: list[float | None] = []
    disk: list[float | None] = []


class SysDiskOut(BaseModel):
    used: int | None = None
    total: int | None = None
    per_day: float | None = None  # 字节 / 天
    days_left: float | None = None
    note: str = ""


class SysResourcesOut(BaseModel):
    sampled_at: datetime | None = None
    version: str | None = None
    cards: list[SysResourceOut] = []
    containers: list[SysContainerOut] = []
    trends: SysTrendOut = SysTrendOut()
    disk: SysDiskOut = SysDiskOut()
    items: list[SysItemOut] = []  # 横幅提示


class SystemOverview(BaseModel):
    mode: str
    today: date
    banner: SysBannerOut
    cards: list[SysCardOut] = []
    grid: SysGridOut | None = None
    timeline: list[SysTodayOut] = []
    sources: list[SysSourceOut] = []
    risk: list[SysRiskOut] = []
    upcoming: list[SysDayOut] = []
    events: list[SysEventOut] = []
    queue: SysQueueOut | None = None
    resources: SysResourcesOut | None = None


class SysAttemptOut(BaseModel):
    attempt: int
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    result: str
    error: str | None


class IntradayQuoteOut(BaseModel):
    symbol: str
    price: float
    prev_close: float | None = None
    change_pct: float | None = None
    quote_time: datetime


class IntradayPositionOut(BaseModel):
    symbol: str
    qty: int
    price: float
    change_pct: float | None = None
    day_pnl: float | None = None  # 隔夜持有部分按昨收、今天成交部分按成交价
    traded_today: bool = False


class IntradayOut(BaseModel):
    """盘中行情（#93，只供查看；交易用收盘日线）。"""

    session: Literal["pre", "regular", "post", "overnight", "closed"]
    session_text: str
    as_of: datetime | None = None
    stale: bool = False
    source: str = "Finnhub"
    note: str = ""
    quotes: list[IntradayQuoteOut] = []
    positions: list[IntradayPositionOut] = []  # 给定 mode 时：该账户持仓的当日盈亏
    day_pnl: float | None = None
