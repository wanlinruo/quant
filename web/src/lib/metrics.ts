import { num, pct } from "./format";

/** 回测指标的显示名与格式（同原面板 METRIC_LABELS）。 */
export const METRICS: { key: string; label: string; kind: "pct" | "num" | "int" }[] = [
  { key: "total_return", label: "总收益", kind: "pct" },
  { key: "cagr", label: "年化收益", kind: "pct" },
  { key: "volatility", label: "年化波动", kind: "pct" },
  { key: "max_drawdown", label: "最大回撤", kind: "pct" },
  { key: "sharpe", label: "Sharpe", kind: "num" },
  { key: "calmar", label: "Calmar", kind: "num" },
  { key: "turnover", label: "年化换手", kind: "num" },
  { key: "trades", label: "交易次数", kind: "int" },
];

export function fmtMetric(v: unknown, kind: "pct" | "num" | "int"): string {
  if (typeof v !== "number") return "-";
  if (kind === "pct") return pct(v);
  if (kind === "num") return num(v);
  return String(v);
}
