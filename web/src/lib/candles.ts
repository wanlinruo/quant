import type { Bar } from "../api/types";
import type { Candle } from "../charts/CandleChart";

/** 日线 → K 线；adjust 时按 adj_close / close 折算（复权价缺失时保留原价，不画成 0）。 */
export function toCandles(bars: Bar[], adjust: boolean): Candle[] {
  return bars.map((b) => {
    const a = b.adj_close as number | null;
    const f = adjust && b.close && a != null && Number.isFinite(a) ? a / b.close : 1;
    return { date: b.date, open: b.open * f, high: b.high * f, low: b.low * f, close: b.close * f, volume: b.volume };
  });
}
