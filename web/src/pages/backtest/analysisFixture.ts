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
  },
  risk: {
    episodes: [{ start: "2022-01-03", trough: "2022-10-12", end: "2023-06-01", depth: -0.213, down_days: 196, recovery_days: 160, qqq: -0.34 }],
    capture: { up: 0.82, down: 0.55 },
    table: [{ label: "Beta", value: 0.72, qqq: null }, { label: "年波动", value: 0.19, qqq: 0.22 }],
    rolling_beta: [{ date: "2023-12-29", beta: 0.7 }],
    tail: [{ label: "VaR 95%（日）", value: -0.018 }],
  },
  params: [{ key: "top_n", value: 12, default: 10, changed: true }, { key: "sma", value: 120, default: 120, changed: false }],
  repro: { source: "tiingo", data_end: "2026-10-29", coverage: 0.98, command: "uv run quant backtest --strategy combo --start 2016-10-28 --end 2026-10-29 --param top_n=12 --no-db" },
};
