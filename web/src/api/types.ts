// 由 python -m quant.api.openapi --ts 生成（make api-types），请勿手改

export type Mode = "sim" | "paper" | "live";

export interface AccountInfo {
  mode: "sim" | "paper" | "live";
  label: string;
  has_data: boolean;
}

export interface AccountPoint {
  date: string;
  cash: number;
  net_liquidation: number;
}

export interface BacktestDefaults {
  strategies: string[];
  universe_options: string[];
  universe: string[];
  safe_asset: string;
  lookback: number;
  top_k: number;
  sma_window: number;
  cash: string;
  commission_per_share: string | null;
  min_commission: string | null;
  slippage: string;
  start: string;
  end: string;
}

export interface BacktestRunOut {
  id: number;
  strategy: string;
  status: string;
  start: string;
  end: string;
  params: Record<string, unknown>;
  costs: Record<string, string> | null;
  metrics: Record<string, unknown>;
  error: string;
  created_at: string;
  finished_at: string | null;
}

export interface BacktestSubmit {
  strategy: string;
  start: string;
  end: string;
  universe: string[];
  safe_asset: string;
  lookback: number;
  top_k: number;
  sma_window: number;
  cash: number | string;
  costs: CostsIn;
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  adj_close: number;
  volume: number;
}

export interface BarsOut {
  adjusted: boolean;
  bars: Bar[];
}

export interface CalendarDayOut {
  date: string;
  pnl: number | null;
  ret: number | null;
}

export interface ClosedTradeOut {
  symbol: string;
  open_date: string;
  close_date: string;
  qty: number;
  open_price: number;
  close_price: number;
  pnl: number;
  ret: number;
  days: number;
}

export interface ClosedTradesOut {
  trades: ClosedTradeOut[];
  stats: TradeStatsOut;
}

export interface ContributionRow {
  symbol: string;
  sleeve: "momentum" | "swing" | "both" | "exit" | null;
  pnl: number;
}

export interface CostsIn {
  commission_per_share: number | string | null;
  min_commission: number | string | null;
  slippage: number | string;
}

export interface DataStatusRow {
  symbol: string;
  last_date: string | null;
  rows: number;
  sources: string;
  last_ingested: string | null;
  ok: boolean;
  issues: string;
}

export interface EarningsMove {
  date: string;
  move: number | null;
}

export interface EarningsOut {
  next: string | null;
  held_through: boolean;
  history: EarningsMove[];
}

export interface EquityPoint {
  run_id: number;
  date: string;
  equity: number;
  drawdown: number;
}

export interface FailureOut {
  time: string;
  source: string;
  message: string;
}

export interface FillOut {
  filled_at: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  commission: number;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface HoldingRow {
  symbol: string;
  sleeve: "momentum" | "swing" | "both" | "exit" | null;
  qty: number;
  avg_cost: number;
  price: number | null;
  change_pct: number | null;
  market_value: number;
  weight: number | null;
  target_weight: number;
  deviation: number | null;
  unrealized_pnl: number | null;
  state: "ok" | "near" | "exit" | null;
  state_note: string;
  next_earnings: string | null;
  holding_days: number;
}

export interface JobStatusOut {
  job_name: string;
  trade_date: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string;
}

export interface KeyStatsOut {
  volume_ratio: number | null;
  beta_1y: number | null;
  vol_20d: number | null;
  from_high: number | null;
  to_ma120: number | null;
  next_earnings: string | null;
  days_to_earnings: number | null;
}

export interface Meta {
  trading_mode: "sim" | "paper" | "live";
  broker: string;
  database_configured: boolean;
  accounts: AccountInfo[];
  default_account: "sim" | "paper" | "live";
  data_as_of: string | null;
}

export interface OrderOut {
  created_at: string;
  client_order_id: string;
  symbol: string;
  side: string;
  order_type: string;
  qty: number;
  limit_price: number | null;
  reference_price: number | null;
  status: string;
  filled_qty: number;
  avg_fill_price: number | null;
  reason: string;
}

export interface PerfPoint {
  date: string;
  nav: number;
  portfolio: number;
  qqq: number | null;
  spy: number | null;
  drawdown: number;
}

export interface PerformanceOut {
  points: PerfPoint[];
  rebalance_dates: string[];
}

export interface PeriodOut {
  start: string;
  end: string;
}

export interface PortfolioOverview {
  as_of: string | null;
  net_liquidation: number | null;
  cash: number | null;
  summary: PortfolioSummaryOut;
  risk: RiskOut;
  top5: number | null;
  max_single: number | null;
  exposure: number | null;
  timing: TimingOut | null;
  allocation: SleeveAlloc[];
  earnings_next_week: string[];
  reconcile: ReconcileOut;
}

export interface PortfolioSummaryOut {
  today_pnl: number | null;
  today_return: number | null;
  total_return: number | null;
  qqq_return: number | null;
  spy_return: number | null;
  max_drawdown: number | null;
  current_drawdown: number | null;
  ann_vol: number | null;
  sharpe: number | null;
}

export interface Position {
  snapshot_date: string;
  symbol: string;
  qty: number;
  avg_cost: number;
  market_value: number;
}

export interface PreviewRow {
  side: "BUY" | "SELL";
  symbol: string;
  sleeve: "momentum" | "swing" | "both" | "exit" | null;
  weight_from: number;
  weight_to: number;
  reason: string;
}

export interface QueueStatus {
  queued?: number;
  running?: number;
  done?: number;
  failed?: number;
}

export interface QuoteOut {
  close: number;
  change: number | null;
  change_pct: number | null;
  open: number;
  high: number;
  low: number;
  volume: number;
  turnover: number;
  high_52w: number;
  low_52w: number;
  ret_1m: number | null;
  ytd: number | null;
}

export interface RankPoint {
  date: string;
  rank: number | null;
}

export interface RebalancePreview {
  signal_date: string | null;
  rows: PreviewRow[];
  turnover: number;
  est_commission: number | null;
}

export interface ReconcileOut {
  trading_enabled: boolean;
  last_report_status: string | null;
}

export interface RelationOut {
  held: boolean;
  weight: number | null;
  sleeve: "momentum" | "swing" | "both" | "exit" | null;
  momentum_rank: number | null;
  universe_size: number | null;
  in_sp500: boolean;
  swing_universe: boolean;
  prev_holding: string | null;
  next_holding: string | null;
}

export interface RiskOut {
  beta_spy: number | null;
  corr_qqq: number | null;
  var95_1d: number | null;
}

export interface SignalOut {
  mode: "sim" | "paper" | "live";
  strategy: string;
  signal_date: string;
  data_as_of: string;
  target_weights: Record<string, number>;
  detail?: Record<string, unknown> | null;
}

export interface SleeveAlloc {
  sleeve: "momentum" | "swing" | "cash" | "other";
  actual: number;
  target: number | null;
}

export interface StockProfile {
  symbol: string;
  as_of: string | null;
  adjusted: boolean;
  quote: QuoteOut | null;
  relation: RelationOut;
  periods: Record<string, number | null>;
  periods_qqq: Record<string, number | null>;
  key_stats: KeyStatsOut;
  technicals: TechnicalsOut;
  swing: SwingCheckOut;
  rank_history: RankPoint[];
  earnings: EarningsOut;
  holding_periods: PeriodOut[];
  trades: FillOut[];
}

export interface SubmitResult {
  id: number;
}

export interface SwingCheckOut {
  trend_up: boolean | null;
  above_support: boolean | null;
  dist_exit: number | null;
}

export interface TechnicalsOut {
  rsi14: number | null;
  atr14_pct: number | null;
  rs_20d: number | null;
  rs_60d: number | null;
  ma10: number | null;
  ma20: number | null;
  ma120: number | null;
  dist_ma10: number | null;
  dist_ma20: number | null;
  dist_ma120: number | null;
}

export interface TimingOut {
  factor: number;
  vol_symbol: string;
  vol_20d: number | null;
  threshold: number;
}

export interface TradeStatsOut {
  count: number;
  win_rate: number | null;
  avg_pnl: number | null;
  avg_slippage: number | null;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}
