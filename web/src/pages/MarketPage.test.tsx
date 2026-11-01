import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mkMeta, mockApi, renderPage } from "../test/utils";
import { MarketPage } from "./MarketPage";

describe("MarketPage", () => {
  it("shows pending board and my strategy card", async () => {
    mockApi({
      "/api/portfolio/paper/curve": [200, [{ date: "2026-10-28", cash: 2100, net_liquidation: 101000 }]],
      "/api/signals/latest": [200, [{ strategy: "combo", signal_date: "2026-10-28", data_as_of: "2026-10-28", target_weights: { NVDA: 0.6, META: 0.4 } }]],
      "/api/system/jobs": [200, []],
    });
    renderPage(<MarketPage />, "/");
    expect(screen.getByText("数据接入中")).toBeInTheDocument();
    expect(await screen.findByText("$101,000.00")).toBeInTheDocument();
    expect(screen.getByText(/combo · 2026-10-28 · 2 个标的/)).toBeInTheDocument();
  });
});

describe("MarketPage account", () => {
  it("strategy summary follows account", async () => {
    const fetch = mockApi({
      "/api/portfolio/live/curve": [200, [{ date: "2026-10-28", cash: 100, net_liquidation: 3500 }]],
      "/api/portfolio/live/overview": [200, { as_of: "2026-10-28", net_liquidation: 3500, cash: 100,
        summary: { today_pnl: 35, today_return: 0.01, total_return: 0.02, qqq_return: null, spy_return: null,
                   max_drawdown: null, current_drawdown: null, ann_vol: null, sharpe: null },
        risk: { beta_spy: null, corr_qqq: null, var95_1d: null }, top5: null, max_single: null, exposure: 0.97,
        timing: { factor: 0.5, vol_symbol: "QQQ", vol_20d: 0.35, threshold: 0.3 }, allocation: [],
        earnings_next_week: [], reconcile: { trading_enabled: true, last_report_status: "success" } }],
      "/api/signals/latest": [200, []],
      "/api/system/jobs": [200, []],
    });
    renderPage(<MarketPage />, "/?account=live", mkMeta({ has: { live: true } }), "live");
    expect(await screen.findByText("$3,500.00")).toBeInTheDocument();
    expect(screen.getByText("实盘")).toBeInTheDocument();
    expect(screen.getByText(/择时系数 0.50/)).toBeInTheDocument();
    const modes = fetch.mock.calls
      .map(([input]) => new URL(String(input), "http://x"))
      .filter((u) => u.pathname === "/api/signals/latest")
      .map((u) => u.searchParams.get("mode"));
    expect(modes).toEqual(["live"]);
  });
});
