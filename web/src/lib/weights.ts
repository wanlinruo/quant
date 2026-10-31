import type { Position, SignalOut } from "../api/types";

export type WeightRow = { symbol: string; target: number; actual: number; diff: number };


/** 实际权重 = 市值 / 总市值；差异 = 实际 − 目标。 */
export function weightRows(positions: Position[], signal: SignalOut | undefined): WeightRow[] {
  const total = positions.reduce((a, p) => a + p.market_value, 0);
  const actual = new Map(positions.map((p) => [p.symbol, total ? p.market_value / total : 0]));
  const target = new Map(Object.entries(signal?.target_weights ?? {}));
  const syms = [...new Set([...actual.keys(), ...target.keys()])].sort();
  return syms.map((s) => {
    const a = actual.get(s) ?? 0;
    const t = target.get(s) ?? 0;
    return { symbol: s, target: t, actual: a, diff: a - t };
  });
}
