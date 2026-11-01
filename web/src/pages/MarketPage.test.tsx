import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { currentLocation, mkMeta, mockApi, renderPage } from "../test/utils";
import { MarketPage } from "./MarketPage";

const stat = (value: number | null, prev: number | null = null) => ({ value, prev });
const OVERVIEW = {
  status: "ok", as_of: "2026-10-30", stale: false, generated_at: "2026-10-31T00:40:00Z", members: 503, coverage: 0.99,
  strip: [
    { symbol: "SPY", label: "标普 500", close: 755.72, change: -3.2, change_pct: -0.0042, spark: [1, 2, 3] },
    { symbol: "QQQ", label: "纳指 100", close: 600, change: 1, change_pct: 0.0017, spark: [1, 2] },
    { symbol: "DIA", label: "道指", close: 450, change: 1, change_pct: 0.002, spark: [] },
    { symbol: "IWM", label: "罗素 2000", close: 230, change: 1, change_pct: 0.004, spark: [] },
    { symbol: "GLD", label: "黄金", close: 380, change: 1, change_pct: 0.003, spark: [] },
  ],
  highlights: "标普 500（SPY）-0.42% 收于 755.72，距 52 周高点 -1.8%；信息技术领涨、能源领跌；上涨家数 41%；52 周新高 12 只、新低 3 只。",
  breadth: {
    advancers: stat(206, 300), decliners: stat(290, 200), unchanged: stat(7, 3), new_highs: stat(12, 20), new_lows: stat(3, 1),
    above_50: stat(0.55, 0.58), above_200: stat(0.62, 0.63), up_volume_ratio: stat(0.38, 0.6),
  },
  styles: [{ key: "growth_value", label: "成长 − 价值", long: "IWF", short: "IWD", d1: 0.004, m1: 0.012 }],
  sectors: [
    { symbol: "XLK", name: "信息技术", d1: 0.012, w1: 0.02, m1: 0.05, ytd: 0.2, rel_spy_d1: 0.016, spark: [1, 2] },
    { symbol: "XLE", name: "能源", d1: -0.02, w1: -0.01, m1: -0.03, ytd: 0.05, rel_spy_d1: -0.016, spark: [2, 1] },
  ],
  movers: [
    { symbol: "NVDA", name: "NVIDIA", sector: "信息技术", market_cap: 4.5e12, close: 190, change_pct: 0.06, volume_ratio: 2.4, signals: ["放量突破", "52周新高"], spark: [1, 2] },
    { symbol: "XOM", name: "ExxonMobil", sector: "能源", market_cap: 4.8e11, close: 110, change_pct: -0.04, volume_ratio: 1.1, signals: ["跳空"], spark: [2, 1] },
    { symbol: "AAPL", name: "Apple", sector: "信息技术", market_cap: 5e12, close: 270, change_pct: 0.021, volume_ratio: 0.9, signals: [], spark: [1, 2] },
  ],
  heatmap: {
    "1D": [
      { symbol: "AAPL", name: "Apple", sector: "信息技术", market_cap: 5e12, ret: 0.021 },
      { symbol: "XOM", name: "ExxonMobil", sector: "能源", market_cap: 4.8e11, ret: -0.04 },
    ],
    "1W": [{ symbol: "AAPL", name: "Apple", sector: "信息技术", market_cap: 5e12, ret: 0.05 }],
    "1M": [], YTD: [],
  },
  index_candles: { SPY: [], QQQ: [], DIA: [], IWM: [] },
};
const BASE = {
  "/api/portfolio/paper/curve": [200, [{ date: "2026-10-28", cash: 2100, net_liquidation: 101000 }]],
  "/api/portfolio/paper/overview": [200, null],
  "/api/portfolio/paper/holdings": [200, [{ symbol: "AAPL" }]],
  "/api/signals/latest": [200, []],
  "/api/system/jobs": [200, []],
} as const;

describe("MarketPage", () => {
  it("shows pending when snapshot missing", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, { status: "pending" }] });
    renderPage(<MarketPage />, "/");
    expect((await screen.findAllByText("数据接入中")).length).toBeGreaterThan(3);
    expect(await screen.findByText("$101,000.00")).toBeInTheDocument();
  });

  it("renders ticker strip with pending macro items", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    renderPage(<MarketPage />, "/");
    const strip = await screen.findByTestId("ticker-strip");
    expect(within(strip).getByText("标普 500")).toBeInTheDocument();
    expect(within(strip).getByText("755.72")).toBeInTheDocument();
    expect(within(strip).getByText("-0.42%")).toBeInTheDocument();
    expect(within(strip).getByText("VIX")).toBeInTheDocument();
    expect(within(strip).getAllByText("数据接入中")).toHaveLength(5);
  });

  it("highlights text shown", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    renderPage(<MarketPage />, "/");
    expect(await screen.findByText(/信息技术领涨、能源领跌/)).toBeInTheDocument();
    expect(screen.getByText(/数据截至 2026-10-30/)).toBeInTheDocument();
  });

  it("shows stale warning", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, { ...OVERVIEW, stale: true }] });
    renderPage(<MarketPage />, "/");
    expect(await screen.findByText(/今日数据尚未发布/)).toBeInTheDocument();
  });

  it("movers filter by signal", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    const { user } = renderPage(<MarketPage />, "/");
    const table = await screen.findByTestId("movers");
    expect(within(table).getByText("XOM")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "放量" }));
    expect(within(table).queryByText("XOM")).not.toBeInTheDocument();
    expect(within(table).getByText("NVDA")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "跌幅" }));
    expect(within(table).getByText("XOM")).toBeInTheDocument();
    expect(within(table).queryByText("NVDA")).not.toBeInTheDocument();
  });

  it("movers holdings filter uses account holdings", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    const { user } = renderPage(<MarketPage />, "/");
    const table = await screen.findByTestId("movers");
    await user.click(screen.getByRole("button", { name: "我的持仓" }));
    expect(within(table).getByText("AAPL")).toBeInTheDocument();
    expect(within(table).queryByText("NVDA")).not.toBeInTheDocument();
  });

  it("heatmap period switch", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    const { user } = renderPage(<MarketPage />, "/");
    const hm = await screen.findByTestId("heatmap");
    expect(within(hm).getByText("XOM")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "1周" }));
    expect(within(hm).queryByText("XOM")).not.toBeInTheDocument();
    expect(within(hm).getByText("+5.0%")).toBeInTheDocument();
  });

  it("treemap cells link to stock page with account", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    const { user } = renderPage(<MarketPage />, "/?account=paper");
    const hm = await screen.findByTestId("heatmap");
    await user.click(within(hm).getByText("AAPL"));
    expect(currentLocation()).toBe("/stock/AAPL?account=paper");
  });

  it("strategy summary follows account", async () => {
    const fetch = mockApi({
      "/api/portfolio/live/curve": [200, [{ date: "2026-10-28", cash: 100, net_liquidation: 3500 }]],
      "/api/portfolio/live/overview": [200, null],
      "/api/portfolio/live/holdings": [200, []],
      "/api/signals/latest": [200, []],
      "/api/system/jobs": [200, []],
      "/api/market/overview": [200, { status: "pending" }],
    });
    renderPage(<MarketPage />, "/?account=live", mkMeta({ has: { live: true } }), "live");
    expect(await screen.findByText("$3,500.00")).toBeInTheDocument();
    const modes = fetch.mock.calls
      .map(([input]) => new URL(String(input), "http://x"))
      .filter((u) => u.pathname === "/api/system/jobs" || u.pathname === "/api/signals/latest")
      .map((u) => u.searchParams.get("mode"));
    expect(modes).toEqual(["live", "live"]);
  });
});
