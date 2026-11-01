// 测试用的回测分析样本（#67 A 批）
const dates = Array.from({ length: 40 }, (_, i) => {
  const d = new Date(Date.UTC(2022, 11, 1 + i));
  return d.toISOString().slice(0, 10);
});
const stat = (key: string, label: string, value: number | null, qqq: number | null, kind: "pct" | "ratio" | "int") => ({ key, label, value, qqq, kind });

export const ANALYSIS = {
  id: 128, strategy: "combo", status: "done", start: "2016-10-28", end: "2026-10-29", error: null,
  initial_cash: 100000, duration_s: 185, oos_start: "2023-01-01",
  stats: [
    stat("total_return", "总收益", 1.5, 1.2, "pct"), stat("cagr", "年化", 0.123, 0.101, "pct"),
    stat("sharpe", "夏普", 1.21, 0.9, "ratio"), stat("sortino", "索提诺", 1.6, 1.2, "ratio"),
    stat("max_drawdown", "最大回撤", -0.213, -0.35, "pct"), stat("calmar", "卡玛", 1.16, 0.4, "ratio"),
    stat("volatility", "年波动", 0.19, 0.22, "pct"), stat("win_rate", "胜率", null, null, "pct"),
    stat("profit_factor", "盈亏比", null, null, "ratio"), stat("trades", "交易笔数", null, null, "int"),
  ],
  series: {
    dates, strategy: dates.map((_, i) => 1 + i * 0.01), qqq: dates.map((_, i) => 1 + i * 0.005),
    spy: dates.map((_, i) => 1 + i * 0.004), drawdown: dates.map(() => 0), qqq_drawdown: dates.map(() => -0.01),
  },
  overview: {
    checks: [
      { label: "最大回撤 ≥ -25%", passed: true }, { label: "年化 ≥ QQQ + 3 个百分点", passed: false },
      { label: "夏普 ≥ 1.1", passed: true }, { label: "两个区间夏普都高于 QQQ", passed: true },
      { label: "相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0", passed: null },
    ],
    split: {
      full: { strategy: { cagr: 0.123, sharpe: 1.21, max_drawdown: -0.213 }, qqq: { cagr: 0.101, sharpe: 0.9, max_drawdown: -0.35 } },
      ins: { strategy: { cagr: 0.11, sharpe: 1.1, max_drawdown: -0.2 }, qqq: { cagr: 0.12, sharpe: 0.95, max_drawdown: -0.3 } },
      oos: { strategy: { cagr: 0.15, sharpe: 1.4, max_drawdown: -0.15 }, qqq: { cagr: 0.08, sharpe: 0.7, max_drawdown: -0.35 } },
    },
    yearly: [{ year: 2022, strategy: -0.08, qqq: -0.33 }, { year: 2023, strategy: 0.31, qqq: 0.55 }],
  },
  returns: {
    monthly: [{ year: 2023, months: [0.05, -0.02, null, null, null, null, null, null, null, null, null, null], year_total: 0.029, qqq_year: 0.1 }],
    distribution: [{ lo: -0.05, hi: 0, count: 3 }, { lo: 0, hi: 0.05, count: 7 }],
    best_worst: [{ period: "月", best: 0.09, best_at: "2023-01-31", worst: -0.07, worst_at: "2022-09-30" }],
    rolling: [{ date: "2023-12-29", ret: 0.2, sharpe: 1.3 }],
    sleeves: { dates, series: { momentum: dates.map((_, i) => i * 0.001), swing: dates.map((_, i) => i * 0.0005) } },
  },
  risk: {
    episodes: [{ start: "2022-01-03", trough: "2022-10-12", end: "2023-06-01", depth: -0.213, down_days: 196, recovery_days: 160, qqq: -0.34 }],
    capture: { up: 0.82, down: 0.55 },
    table: [{ label: "Beta", value: 0.72, qqq: null }, { label: "年波动", value: 0.19, qqq: 0.22 }],
    rolling_beta: [{ date: "2023-12-29", beta: 0.7 }],
    tail: [{ label: "VaR 95%（日）", value: -0.018 }],
    timing: [{ start: "2022-12-11", end: "2022-12-15", days: 5, factor: 0.5, ret: -0.03, est_full: -0.06, saved: 0.03 }],
  },
  params: [{ key: "top_n", value: 12, default: 10, changed: true }, { key: "sma", value: 120, default: 120, changed: false }],
  repro: { source: "tiingo", data_end: "2026-10-29", coverage: 0.98, code_version: "abc1234", log: ["10:00:00 INFO 开始回测", "10:00:05 WARNING NVDA 数据中断"], command: "uv run quant backtest --strategy combo --start 2016-10-28 --end 2026-10-29 --param top_n=12 --no-db" },
};

export const TRADES = {
  note: null,
  summary: { trades: 3, closed: 2, win_rate: 0.5, profit_factor: 4.0, avg_days: 12.5, avg_ret: 0.05,
             best: { symbol: "AAA", pnl: 200, ret: 0.2 }, worst: { symbol: "BBB", pnl: -50, ret: -0.1 } },
  trades: [
    { seq: 1, symbol: "AAA", sleeve: "momentum", open_date: "2022-12-05", open_price: 10, close_date: "2022-12-20", close_price: 12, qty: 100, ret: 0.2, pnl: 200, exit_reason: "动量排名跌出前 10", days: 11, adjustments: [{ date: "2022-12-10", qty: 20 }] },
    { seq: 2, symbol: "BBB", sleeve: "swing", open_date: "2022-12-08", open_price: 50, close_date: "2022-12-22", close_price: 45, qty: 10, ret: -0.1, pnl: -50, exit_reason: "跌破支撑线", days: 14, adjustments: [] },
    { seq: 3, symbol: "CCC", sleeve: "momentum", open_date: "2023-01-03", open_price: 20, close_date: null, close_price: null, qty: 5, ret: 0.05, pnl: 5, exit_reason: null, days: 7, adjustments: [] },
  ],
  by_reason: [{ reason: "动量排名跌出前 10", count: 1, win_rate: 1, avg_ret: 0.2, pnl: 200 }, { reason: "跌破支撑线", count: 1, win_rate: 0, avg_ret: -0.1, pnl: -50 }],
  by_symbol: [{ symbol: "AAA", trades: 1, pnl: 200, win_rate: 1, days: 11 }, { symbol: "BBB", trades: 1, pnl: -50, win_rate: 0, days: 14 }],
};

export const POSITIONS = {
  note: null,
  daily: dates.map((d, i) => ({ date: d, cash: 0.2, sleeves: { momentum: 0.4, swing: 0.4 }, timing_factor: i >= 10 && i < 15 ? 0.5 : 1 })),
  timing: [{ start: dates[10], end: dates[14], days: 5, factor: 0.5, ret: -0.03, est_full: -0.06, saved: 0.03 }],
  day: dates[39],
  holdings: [{ symbol: "AAA", sleeve: "momentum", weight: 0.25 }, { symbol: "BBB", sleeve: "swing", weight: 0.15 }],
  longest: [{ symbol: "AAA", sleeve: "momentum", days: 120, open_date: "2022-01-03", close_date: "2022-06-30" }],
};
