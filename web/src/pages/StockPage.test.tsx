import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { StockPage } from "./StockPage";

const BARS = {
  adjusted: true,
  bars: [
    { date: "2026-10-29", open: 1, high: 1, low: 1, close: 100, adj_close: 100, volume: 1 },
    { date: "2026-10-30", open: 1, high: 1, low: 1, close: 103.4, adj_close: 103.4, volume: 1 },
  ],
};
const NONE = { "1D": null, "1W": null, "1M": null, "3M": null, "6M": null, YTD: null, "1Y": null };
const EMPTY_PROFILE = {
  symbol: "ZZZZ", as_of: null, adjusted: true, quote: null,
  relation: { held: false, weight: null, sleeve: null, momentum_rank: null, universe_size: null, in_sp500: false,
              swing_universe: false, prev_holding: null, next_holding: null },
  periods: {}, periods_qqq: {},
  key_stats: { volume_ratio: null, beta_1y: null, vol_20d: null, from_high: null, to_ma120: null, next_earnings: null, days_to_earnings: null },
  technicals: { rsi14: null, atr14_pct: null, rs_20d: null, rs_60d: null, ma10: null, ma20: null, ma120: null, dist_ma10: null, dist_ma20: null, dist_ma120: null },
  swing: { trend_up: null, above_support: null, dist_exit: null },
  rank_history: [], earnings: { next: null, held_through: false, history: [] }, holding_periods: [], trades: [],
};
const PROFILE = {
  ...EMPTY_PROFILE,
  symbol: "NVDA", as_of: "2026-10-30",
  quote: { close: 103.4, change: 3.4, change_pct: 0.034, open: 101, high: 104, low: 100, volume: 1e6, turnover: 1.034e8,
           high_52w: 120, low_52w: 80, ret_1m: 0.05, ytd: 0.2 },
  relation: { held: true, weight: 0.12, sleeve: "swing", momentum_rank: 3, universe_size: 503, in_sp500: true,
              swing_universe: true, prev_holding: "AAPL", next_holding: "META" },
  periods: { ...NONE, "1D": 0.034 }, periods_qqq: { ...NONE, "1D": 0.01 },
  swing: { trend_up: true, above_support: true, dist_exit: 0.012 },
  earnings: { next: "2026-11-19", held_through: true, history: [{ date: "2026-08-27", move: -0.03 }] },
  holding_periods: [{ start: "2026-10-01", end: "2026-10-30" }],
  trades: [{ filled_at: "2026-10-01T14:00:00Z", symbol: "NVDA", side: "BUY", qty: 300, price: 130.5, commission: 1.17 }],
};

function render(symbol: string, routes: Record<string, readonly [number, unknown]>, path?: string) {
  mockApi(routes);
  return renderPage(<Routes><Route path="/stock/:symbol" element={<StockPage />} /></Routes>, path ?? `/stock/${symbol}?account=paper`);
}

describe("StockPage", () => {
  it("shows last close and change", async () => {
    render("NVDA", { "/api/market/bars/NVDA": [200, BARS], "/api/stock/NVDA/profile": [200, PROFILE] });
    expect(await screen.findByText("103.40")).toBeInTheDocument();
    expect(screen.getAllByText("+3.40%").length).toBeGreaterThan(0);
  });

  it("renders held stock relation", async () => {
    render("NVDA", { "/api/market/bars/NVDA": [200, BARS], "/api/stock/NVDA/profile": [200, PROFILE] });
    expect(await screen.findByText("持有中 · 波段仓 · 12.0%")).toBeInTheDocument();
    expect(screen.getByText("动量排名 3 / 503")).toBeInTheDocument();
    expect(screen.getByText("标普 500 成分")).toBeInTheDocument();
  });

  it("renders empty stock", async () => {
    render("ZZZZ", {
      "/api/market/bars/ZZZZ": [200, { adjusted: true, bars: [] }],
      "/api/stock/ZZZZ/profile": [200, EMPTY_PROFILE],
    });
    expect(await screen.findByText("ZZZZ")).toBeInTheDocument();
    expect((await screen.findAllByText("暂无数据")).length).toBeGreaterThan(0);
    expect(screen.queryByText("加载中…")).not.toBeInTheDocument();
  });

  it("prev and next holding navigation keeps account", async () => {
    render("NVDA", { "/api/market/bars/NVDA": [200, BARS], "/api/stock/NVDA/profile": [200, PROFILE] });
    expect(await screen.findByRole("link", { name: "下一只 META ›" })).toHaveAttribute("href", "/stock/META?account=paper");
    expect(screen.getByRole("link", { name: "‹ 上一只 AAPL" })).toHaveAttribute("href", "/stock/AAPL?account=paper");
  });

  it("shows 未复权 when not adjusted", async () => {
    render("AMD", {
      "/api/market/bars/AMD": [200, { ...BARS, adjusted: false }],
      "/api/stock/AMD/profile": [200, { ...PROFILE, symbol: "AMD", adjusted: false }],
    });
    expect(await screen.findByText("未复权")).toBeInTheDocument();
  });

  it("trade row click selects marker", async () => {
    const { user } = render("NVDA", { "/api/market/bars/NVDA": [200, BARS], "/api/stock/NVDA/profile": [200, PROFILE] });
    const table = await screen.findByTestId("trades");
    const row = within(table).getByText("买入").closest("tr");
    expect(row).not.toBeNull();
    await user.click(row as HTMLElement);
    expect(row).toHaveAttribute("aria-selected", "true");
  });

  it("pending modules show 数据接入中", async () => {
    render("NVDA", { "/api/market/bars/NVDA": [200, BARS], "/api/stock/NVDA/profile": [200, PROFILE] });
    await screen.findByText("持有中 · 波段仓 · 12.0%");
    expect(screen.getAllByText("数据接入中").length).toBeGreaterThanOrEqual(4);
  });
});
