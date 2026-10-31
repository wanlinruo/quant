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

export interface JobStatusOut {
  job_name: string;
  trade_date: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string;
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

export interface Position {
  snapshot_date: string;
  symbol: string;
  qty: number;
  avg_cost: number;
  market_value: number;
}

export interface QueueStatus {
  queued?: number;
  running?: number;
  done?: number;
  failed?: number;
}

export interface SignalOut {
  strategy: string;
  signal_date: string;
  data_as_of: string;
  target_weights: Record<string, number>;
}

export interface SubmitResult {
  id: number;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}
