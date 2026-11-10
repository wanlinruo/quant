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

const METRICS = [
  { key: "peTTM", label: "市盈率（TTM）", kind: "ratio", value: 39.0103 },
  { key: "roeTTM", label: "ROE（TTM）", kind: "pct", value: 1.3718 },
  { key: "grossMarginTTM", label: "毛利率", kind: "pct", value: null },
];
const EXTRAS = {
  symbol: "NVDA", covered: true, name: "Nvidia", sector: "信息技术", sub_industry: "Semiconductors", market_cap: 4.5e12,
  sector_etf: "XLK", sector_periods: { ...NONE, "1D": 0.012, "1Y": 0.25 },
  peers: [
    { symbol: "AVGO", name: "Broadcom", market_cap: 1.6e12, d1: 0.021, ytd: 0.4 },
    { symbol: "AMD", name: "Advanced Micro Devices", market_cap: 2.4e11, d1: -0.03, ytd: null },
  ],
  metrics: METRICS, metrics_updated: "2026-10-30",
  news: Array.from({ length: 7 }, (_, i) => ({
    time: `2026-10-30T${String(20 - i).padStart(2, "0")}:00:00Z`, headline: i === 0 ? "Nvidia Q3 earnings beat" : `Nvidia headline ${i}`,
    source: "CNBC", url: `https://n/${i}`, tags: ["NVDA"], category: i === 0 ? "earnings" : "company",
  })),
  news_updated: "2026-10-30T21:15:00-04:00",
};
const UNCOVERED = { symbol: "QQQ", covered: false, sector_periods: {}, peers: [], metrics: [], news: [] };
const ROUTES = { "/api/market/bars/NVDA": [200, BARS], "/api/stock/NVDA/profile": [200, PROFILE], "/api/stock/NVDA/extras": [200, EXTRAS] } as const;

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

  it("stock extras sector etf row", async () => {
    render("NVDA", ROUTES);
    const row = (await screen.findByText("板块 ETF · XLK 信息技术")).closest("tr")!;
    expect(within(row).getByText("+1.20%")).toBeInTheDocument();
    expect(within(row).getByText("+25.00%")).toBeInTheDocument();
  });

  it("stock extras key stats cap and sector", async () => {
    render("NVDA", ROUTES);
    expect(await screen.findByText("$4.50T")).toBeInTheDocument();
    expect(screen.getByTitle("Semiconductors")).toHaveTextContent("信息技术");
  });

  it("stock extras metrics", async () => {
    render("NVDA", ROUTES);
    const panel = await screen.findByTestId("metrics");
    expect(within(panel).getByText("39.01×")).toBeInTheDocument();
    expect(within(panel).getByText("137.2%")).toBeInTheDocument();
    expect(within(panel).getByText("毛利率").closest("tr")).toHaveTextContent("—");
    expect(screen.getByText("更新于 10-30")).toBeInTheDocument();
  });

  it("stock extras news panel and tab", async () => {
    const { user } = render("NVDA", ROUTES);
    const side = await screen.findByTestId("stock-news-side");
    expect(within(side).getAllByRole("link")).toHaveLength(5);
    expect(within(side).getByRole("link", { name: "Nvidia Q3 earnings beat" })).toHaveAttribute("rel", "noreferrer");
    expect(within(side).getByText("10-30 16:00")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "新闻" }));
    const tab = await screen.findByTestId("stock-news-tab");
    expect(within(tab).getAllByRole("link")).toHaveLength(7);
    expect(within(tab).getByText("财报")).toBeInTheDocument();
  });

  it("stock extras peers link with account", async () => {
    render("NVDA", ROUTES);
    const peers = await screen.findByTestId("peers");
    expect(within(peers).getByRole("link", { name: "AVGO" })).toHaveAttribute("href", "/stock/AVGO?account=paper");
    expect(within(peers).getByText("$1.60T")).toBeInTheDocument();
    expect(within(peers).getByText("-3.00%")).toBeInTheDocument();
  });

  it("stock extras uncovered", async () => {
    render("QQQ", {
      "/api/market/bars/QQQ": [200, BARS],
      "/api/stock/QQQ/profile": [200, { ...PROFILE, symbol: "QQQ" }],
      "/api/stock/QQQ/extras": [200, UNCOVERED],
    });
    await screen.findByText("持有中 · 波段仓 · 12.0%");
    expect(await screen.findAllByText("仅覆盖标普 500 成分股与持仓")).toHaveLength(3);
  });

  it("stock extras holding outside sp500 and no recent news", async () => {
    const ext = { symbol: "NVDA", covered: true, in_sp500: false, sector_periods: {}, peers: [], metrics: [], news: [], news_updated: "2026-10-30T21:15:00-04:00" };
    render("NVDA", { ...ROUTES, "/api/stock/NVDA/extras": [200, ext] });
    expect(await screen.findByText("非标普 500 成分股，无同板块数据")).toBeInTheDocument();
    expect(screen.getAllByText("近 7 天无相关新闻").length).toBeGreaterThan(0);
  });

  it("stock news shows update time", async () => {
    render("NVDA", ROUTES);
    const side = await screen.findByTestId("stock-news-side");
    expect(within(side.closest("section")!).getByText("更新于 10-30 21:15 ET")).toBeInTheDocument();
  });
});
