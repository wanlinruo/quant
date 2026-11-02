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
  "/api/market/news": [200, { status: "pending" }],
  "/api/market/calendar": [200, { status: "pending" }],
} as const;

const MACRO = [
  { symbol: "VIX", label: "VIX", close: 17.5, change: 0.5, change_pct: 0.0294, spark: [17, 17.5], as_of: "2026-10-29", note: "", unit: "price" },
  { symbol: "US10Y", label: "美债 10 年", close: 4.98, change: 0.05, change_pct: null, spark: [4.93, 4.98], as_of: "2026-10-30", note: "", unit: "yield" },
  { symbol: "USD", label: "美元指数", close: 121.3, change: -0.2, change_pct: -0.0016, spark: [], as_of: "2026-10-23", note: "广义美元指数 · 周", unit: "price" },
  { symbol: "WTI", label: "原油", close: 103.4, change: 1, change_pct: 0.0098, spark: [], as_of: "2026-10-30", note: "WTI", unit: "price" },
  { symbol: "BTC", label: "比特币", close: 77756.72, change: 333, change_pct: 0.0043, spark: [], as_of: "2026-10-31", note: "Coinbase · UTC 日线", unit: "price" },
];
const NEWS = {
  status: "ok", generated_at: "2026-10-30T21:00:00-04:00",
  items: [
    { time: "2026-10-30T20:10:00Z", headline: "Exxon Q3 earnings beat", source: "CNBC", url: "https://n/1", tags: ["XOM"], category: "earnings" },
    { time: "2026-10-30T18:00:00Z", headline: "Exxon names CFO", source: "Reuters", url: "https://n/2", tags: ["XOM"], category: "company" },
    { time: "2026-10-29T14:00:00Z", headline: "Fed holds rates", source: "Reuters", url: "https://n/3", tags: [], category: "macro" },
  ],
};
const CAL = {
  status: "ok", generated_at: "2026-10-30T21:00:00-04:00",
  released: [
    { time: "2026-10-30T08:30:00-04:00", date_only: false, kind: "econ", title: "Non-Farm Employment Change", impact: 3, actual: "63K", forecast: "60K", previous: "29K", symbols: [], held: false, session: null },
  ],
  upcoming: [
    { time: "2026-11-02T08:00:00-05:00", date_only: false, kind: "earnings", title: "CVX · XOM 财报", impact: 2, actual: null, forecast: "2.43", previous: null, symbols: ["CVX", "XOM"], held: false, session: "盘前" },
    { time: "2026-11-04T12:00:00-05:00", date_only: true, kind: "earnings", title: "AAPL 财报", impact: 3, actual: null, forecast: null, previous: null, symbols: ["AAPL"], held: true, session: null },
  ],
};

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

  it("held-only rows appear only under holdings filter", async () => {
    const held = { symbol: "MSFT", name: "Microsoft", sector: "信息技术", market_cap: 4e12, close: 500, change_pct: 0.003, volume_ratio: 1, signals: [], spark: [], held: true };
    mockApi({ ...BASE, "/api/portfolio/paper/holdings": [200, [{ symbol: "MSFT" }]], "/api/market/overview": [200, { ...OVERVIEW, movers: [...OVERVIEW.movers, held] }] });
    const { user } = renderPage(<MarketPage />, "/");
    const table = await screen.findByTestId("movers");
    expect(within(table).queryByText("MSFT")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "我的持仓" }));
    expect(within(table).getByText("MSFT")).toBeInTheDocument();
  });

  it("strip shows macro items in order with yield in bp", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, { ...OVERVIEW, macro: MACRO }] });
    renderPage(<MarketPage />, "/");
    const strip = await screen.findByTestId("ticker-strip");
    const labels = [...strip.querySelectorAll(".strip-label")].map((e) => e.textContent);
    expect(labels).toEqual(["标普 500", "纳指 100", "道指", "罗素 2000", "VIX", "美债 10 年", "美元指数", "原油", "黄金", "比特币"]);
    expect(within(strip).getByText("+5bp")).toBeInTheDocument();
    expect(within(strip).getByText("截至 10-29")).toBeInTheDocument();
    expect(within(strip).getByText("WTI")).toBeInTheDocument();
    expect(within(strip).queryByText("数据接入中")).not.toBeInTheDocument();
  });

  it("macro shown even when snapshot pending", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, { status: "pending", macro: MACRO.slice(0, 1) }] });
    renderPage(<MarketPage />, "/");
    const strip = await screen.findByTestId("ticker-strip");
    expect(await within(strip).findByText("17.50")).toBeInTheDocument();
    expect(within(strip).getAllByText("数据接入中")).toHaveLength(9);
  });

  it("news filters", async () => {
    mockApi({ ...BASE, "/api/portfolio/paper/holdings": [200, [{ symbol: "XOM" }]], "/api/market/overview": [200, OVERVIEW], "/api/market/news": [200, NEWS] });
    const { user } = renderPage(<MarketPage />, "/");
    const panel = await screen.findByTestId("news");
    expect(await within(panel).findAllByRole("link")).toHaveLength(3);
    expect(within(panel).getByText("10-29")).toBeInTheDocument();
    expect(within(panel).getByText("16:10")).toBeInTheDocument();
    await user.click(within(panel.closest("section")!).getByRole("button", { name: "宏观" }));
    expect(within(panel).getAllByRole("link")).toHaveLength(1);
    await user.click(within(panel.closest("section")!).getByRole("button", { name: "持仓" }));
    expect(within(panel).getAllByRole("link")).toHaveLength(2);
    await user.click(within(panel.closest("section")!).getByRole("button", { name: "财报" }));
    expect(within(panel).getByRole("link", { name: "Exxon Q3 earnings beat" })).toHaveAttribute("rel", "noreferrer");
    expect(within(panel).getAllByRole("link")).toHaveLength(1);
  });

  it("news shows update time", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW], "/api/market/news": [200, { ...NEWS, generated_at: "2026-10-28T21:00:00-04:00" }] });
    renderPage(<MarketPage />, "/");
    const panel = await screen.findByTestId("news");
    expect(await within(panel).findByText("更新于 10-28 21:00 ET")).toBeInTheDocument();
  });

  it("calendar released and upcoming", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW], "/api/market/calendar": [200, CAL] });
    renderPage(<MarketPage />, "/");
    const panel = await screen.findByTestId("calendar");
    expect(await within(panel).findByText("实际 63K")).toBeInTheDocument();
    expect(within(panel).getByText("预期 60K · 前值 29K")).toBeInTheDocument();
    expect(within(panel).getByText(/盘前/)).toBeInTheDocument();
    expect(within(panel).getByText(/时间未定/)).toBeInTheDocument();
    const dots = panel.querySelectorAll("[data-upcoming] .dots");
    expect([...dots].map((d) => d.querySelectorAll("[data-on]").length)).toEqual([2, 3]);
    expect(within(panel).getByText("经济数据每周一更新")).toBeInTheDocument();
  });

  it("mover reason column", async () => {
    const movers = OVERVIEW.movers.map((x) => (x.symbol === "NVDA" ? { ...x, reason: { headline: "Nvidia extends gains", source: "Reuters", time: "2026-10-30T18:00:00Z", url: "https://n/9" } } : x));
    mockApi({ ...BASE, "/api/market/overview": [200, { ...OVERVIEW, movers }] });
    renderPage(<MarketPage />, "/");
    const table = await screen.findByTestId("movers");
    const nvda = within(table).getByText("NVDA").closest("tr")!;
    expect(within(nvda).getByText("Nvidia extends gains · Reuters 14:00")).toBeInTheDocument();
    const xom = within(table).getByText("XOM").closest("tr")!;
    expect(within(xom).getByTestId("reason")).toHaveTextContent("—");
  });

  it("news and calendar pending", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW] });
    renderPage(<MarketPage />, "/");
    expect(await within(await screen.findByTestId("news")).findByText("数据接入中")).toBeInTheDocument();
    expect(await within(await screen.findByTestId("calendar")).findByText("数据接入中")).toBeInTheDocument();
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

  it("sector panel notes price return and heatmap pending note", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, { ...OVERVIEW, heatmap: { "1D": [], "1W": [], "1M": [], YTD: [] } }] });
    renderPage(<MarketPage />, "/");
    expect(await screen.findByText(/价格涨跌，不含分红/)).toBeInTheDocument();
    expect(screen.getByText("市值每天补 60 只，约 9 个交易日补齐")).toBeInTheDocument();
  });

  it("intraday panel shows index and holdings with today's P&L during regular hours", async () => {
    const q = (symbol: string, price: number, prev: number) => ({ symbol, price, prev_close: prev, change_pct: price / prev - 1, quote_time: "2026-11-02T15:00:00Z" });
    const INTRA = { session: "regular", session_text: "盘中", as_of: "2026-11-02T15:00:00Z", stale: false, source: "Finnhub",
      note: "Finnhub 盘中报价 · 仅供查看，交易用收盘日线", quotes: [q("SPY", 760, 755.72), q("QQQ", 700, 690.49), q("MSFT", 410, 400)] };
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW], "/api/market/intraday": [200, INTRA],
      "/api/portfolio/paper/holdings": [200, [{ symbol: "MSFT", qty: 10 }, { symbol: "QQQ", qty: 2 }]] });
    renderPage(<MarketPage />, "/");
    const panel = await screen.findByTestId("intraday");
    expect(within(panel).getByText("仅供查看", { exact: false })).toBeInTheDocument();
    const msft = within(panel).getByText("MSFT").closest("tr") as HTMLElement;
    expect(msft).toHaveTextContent("+$100");  // 10 × (410 − 400)
    expect(within(panel).getByTestId("intraday-total")).toHaveTextContent("+$119");  // 100 + 2 × 9.51
  });

  it("intraday panel outside regular hours only explains the session", async () => {
    mockApi({ ...BASE, "/api/market/overview": [200, OVERVIEW],
      "/api/market/intraday": [200, { session: "pre", session_text: "盘前", stale: false, source: "Finnhub", note: "盘前：扩展时段行情未接入，显示上一交易日收盘", quotes: [] }] });
    renderPage(<MarketPage />, "/");
    const panel = await screen.findByTestId("intraday");
    expect(panel).toHaveTextContent("盘前：扩展时段行情未接入");
    expect(panel.querySelector("table")).toBeNull();
  });
});
