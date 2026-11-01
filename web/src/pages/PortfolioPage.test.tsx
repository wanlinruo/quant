import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { PortfolioPage } from "./PortfolioPage";

const EMPTY = {
  "/api/portfolio/paper/curve": [200, []],
  "/api/portfolio/paper/positions": [200, []],
  "/api/portfolio/paper/orders": [200, []],
  "/api/portfolio/paper/fills": [200, []],
  "/api/signals/latest": [200, []],
} as const;

describe("PortfolioPage", () => {
  it("shows database warning on 503", async () => {
    const nodb = [503, { detail: "未配置数据库（QUANT_DATABASE_URL）" }] as [number, unknown];
    mockApi(Object.fromEntries(Object.keys(EMPTY).map((k) => [k, nodb])));
    renderPage(<PortfolioPage />, "/portfolio");
    expect((await screen.findAllByText("未配置数据库（QUANT_DATABASE_URL）")).length).toBeGreaterThan(0);
  });

  it("shows empty state for account without data", async () => {
    mockApi({ ...EMPTY });
    renderPage(<PortfolioPage />, "/portfolio");
    expect((await screen.findAllByText("暂无数据")).length).toBeGreaterThan(0);
  });

  it("computes weight diff as actual minus target", async () => {
    mockApi({
      ...EMPTY,
      "/api/portfolio/paper/curve": [200, [{ date: "2026-10-28", cash: 2100, net_liquidation: 101000 }]],
      "/api/portfolio/paper/positions": [200, [
        { snapshot_date: "2026-10-28", symbol: "NVDA", qty: 1, avg_cost: 1, market_value: 60 },
        { snapshot_date: "2026-10-28", symbol: "META", qty: 1, avg_cost: 1, market_value: 40 },
      ]],
      "/api/signals/latest": [200, [
        { strategy: "combo", signal_date: "2026-10-28", data_as_of: "2026-10-28", target_weights: { NVDA: 0.5, META: 0.5 } },
      ]],
    });
    renderPage(<PortfolioPage />, "/portfolio");
    const nvda = (await screen.findByText("NVDA", { selector: "td" })).closest("tr")!;
    expect(within(nvda).getByText("+10.0%")).toBeInTheDocument();
    const meta = screen.getByText("META", { selector: "td" }).closest("tr")!;
    expect(within(meta).getByText("-10.0%")).toBeInTheDocument();
    expect(screen.getByText("$101,000.00")).toBeInTheDocument();
  });

  it("queries the live account when selected", async () => {
    const spy = mockApi({});
    renderPage(<PortfolioPage />, "/portfolio?account=live", undefined, "live");
    await screen.findAllByText(/请求失败|暂无数据|Not Found/);
    const urls = spy.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.startsWith("/api/portfolio/live/curve"))).toBe(true);
  });
});
