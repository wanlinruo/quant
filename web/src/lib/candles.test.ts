import { describe, expect, it } from "vitest";
import { toCandles } from "./candles";

const bar = (close: number, adj: number | null) => ({ date: "2026-10-30", open: close, high: close, low: close, close, adj_close: adj as number, volume: 1 });

describe("toCandles", () => {
  it("scales by adj_close / close when adjusting", () => {
    expect(toCandles([bar(100, 50)], true)[0].close).toBe(50);
    expect(toCandles([bar(100, 50)], false)[0].close).toBe(100);
  });

  it("keeps raw prices when adj_close is missing", () => {
    expect(toCandles([bar(100, null)], true)[0].close).toBe(100);
    expect(toCandles([bar(100, Number.NaN)], true)[0].close).toBe(100);
  });
});
