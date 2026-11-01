import { describe, expect, it } from "vitest";
import { squarify } from "../lib/treemap";

describe("squarify", () => {
  it("areas are proportional and fill the canvas", () => {
    const rects = squarify([6, 6, 4, 3, 2, 2, 1], { x: 0, y: 0, w: 600, h: 400 });
    const total = rects.reduce((a, r) => a + r.w * r.h, 0);
    expect(total).toBeCloseTo(600 * 400, 3);
    const unit = (600 * 400) / 24;
    [6, 6, 4, 3, 2, 2, 1].forEach((v, i) => expect(rects[i].w * rects[i].h).toBeCloseTo(v * unit, 3));
    for (const r of rects) {
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(600 + 1e-6);
      expect(r.y + r.h).toBeLessThanOrEqual(400 + 1e-6);
    }
  });

  it("handles empty and zero values", () => {
    expect(squarify([], { x: 0, y: 0, w: 10, h: 10 })).toEqual([]);
    expect(squarify([0, 0], { x: 0, y: 0, w: 10, h: 10 })).toHaveLength(2);
  });
});
