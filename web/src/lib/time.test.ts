import { describe, expect, it } from "vitest";
import { etYearsAgo } from "./time";

describe("etYearsAgo", () => {
  it("uses the New York date, not UTC", () => {
    // 北京时间 11-02 早上 9 点 = UTC 11-02 01:00 = 美东 11-01 21:00
    expect(etYearsAgo(2, new Date("2026-11-02T01:00:00Z"))).toBe("2024-11-01");
    expect(etYearsAgo(1, new Date("2026-11-02T15:00:00Z"))).toBe("2025-11-02");
  });
  it("clamps Feb 29", () => {
    expect(etYearsAgo(1, new Date("2028-02-29T17:00:00Z"))).toBe("2027-02-28");
  });
});
