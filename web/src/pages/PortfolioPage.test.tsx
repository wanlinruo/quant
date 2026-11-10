import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { currentLocation, mkMeta, mockApi, renderPage } from "../test/utils";
import { PortfolioPage } from "./PortfolioPage";

const SUMMARY = {
  today_pnl: 1000, today_return: 0.0099, total_return: 0.01, qqq_return: 0.02, spy_return: 0.015,
  max_drawdown: -0.05, current_drawdown: -0.01, ann_vol: 0.18, sharpe: 1.2,
};
const OVERVIEW = {
  as_of: "2026-10-28", net_liquidation: 101000, cash: 2100, summary: SUMMARY,
  risk: { beta_spy: 1.1, corr_qqq: 0.8, var95_1d: 2500 },
  top5: 0.98, max_single: 0.59, exposure: 0.98,
  timing: { factor: 1, vol_symbol: "QQQ", vol_20d: 0.22, threshold: 0.3 },
  allocation: [
    { sleeve: "momentum", actual: 0.38, target: 0.4 },
    { sleeve: "swing", actual: 0.6, target: 0.6 },
    { sleeve: "cash", actual: 0.02, target: 0 },
  ],
  earnings_next_week: ["NVDA"],
  reconcile: { trading_enabled: true, last_report_status: "success" },
};
const HOLDINGS = [
  { symbol: "NVDA", sleeve: "swing", qty: 300, avg_cost: 130, price: 200, change_pct: 0.01, market_value: 60000,
    weight: 0.594, target_weight: 0.6, deviation: -0.006, unrealized_pnl: 21000, state: "near",
    state_note: "距 10 日线 +1.2%", next_earnings: "2026-11-19", holding_days: 12 },
  { symbol: "META", sleeve: "momentum", qty: 60, avg_cost: 600, price: 648, change_pct: -0.02, market_value: 38900,
    weight: 0.385, target_weight: 0.4, deviation: -0.015, unrealized_pnl: 2880, state: "ok",
    state_note: "排名 3/10", next_earnings: null, holding_days: 30 },
];
const ROUTES = {
  "/api/portfolio/paper/overview": [200, OVERVIEW],
  "/api/portfolio/paper/performance": [200, { points: [], rebalance_dates: [] }],
  "/api/portfolio/paper/holdings": [200, HOLDINGS],
  "/api/portfolio/paper/contributions": [200, [{ symbol: "NVDA", sleeve: "swing", pnl: 600 }]],
  "/api/portfolio/paper/calendar": [200, [{ date: "2026-10-28", pnl: 1000, ret: 0.0099 }]],
  "/api/portfolio/paper/rebalance-preview": [200, {
    signal_date: "2026-10-28", turnover: 0.1, est_commission: 3.2,
    rows: [{ side: "BUY", symbol: "AMD", sleeve: "momentum", weight_from: 0, weight_to: 0.04, reason: "动量排名第 1" }],
  }],
  "/api/portfolio/paper/closed-trades": [200, {
    trades: [{ symbol: "AAPL", open_date: "2026-10-01", close_date: "2026-10-20", qty: 10, open_price: 200,
               close_price: 220, pnl: 200, ret: 0.1, days: 19 }],
    stats: { count: 1, win_rate: 1, avg_pnl: 200, avg_slippage: 0.0012 },
  }],
  "/api/portfolio/paper/orders": [200, []],
  "/api/portfolio/paper/fills": [200, []],
} as const;

function withMode(mode: string) {
  return Object.fromEntries(Object.entries(ROUTES).map(([k, v]) => [k.replace("/paper/", `/${mode}/`), v]));
}

describe("PortfolioPage", () => {
  it("shows database warning on 503", async () => {
    const nodb = [503, { detail: "未配置数据库（QUANT_DATABASE_URL）" }] as [number, unknown];
    mockApi(Object.fromEntries(Object.keys(ROUTES).map((k) => [k, nodb])));
    renderPage(<PortfolioPage />, "/portfolio");
    expect((await screen.findAllByText("未配置数据库（QUANT_DATABASE_URL）")).length).toBeGreaterThan(0);
  });

  it("renders six stat cards with live label", async () => {
    mockApi(withMode("live"));
    renderPage(<PortfolioPage />, "/portfolio?account=live", mkMeta({ has: { live: true } }), "live");
    const cards = await screen.findAllByTestId("stat-card");
    expect(cards).toHaveLength(6);
    for (const c of cards) expect(within(c).getByText("实盘")).toBeInTheDocument();
    expect(screen.getByText("$101,000.00")).toBeInTheDocument();
  });

  it("shows 数据不足 for null metrics", async () => {
    const nulls = Object.fromEntries(Object.keys(SUMMARY).map((k) => [k, null]));
    mockApi({
      ...ROUTES,
      "/api/portfolio/paper/overview": [200, { ...OVERVIEW, summary: nulls, risk: { beta_spy: null, corr_qqq: null, var95_1d: null } }],
    });
    renderPage(<PortfolioPage />, "/portfolio");
    expect((await screen.findAllByText("数据不足")).length).toBeGreaterThan(0);
  });

  it("filters holdings by sleeve", async () => {
    mockApi(ROUTES);
    const { user } = renderPage(<PortfolioPage />, "/portfolio");
    expect(await screen.findByRole("link", { name: "META" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "波段仓" }));
    expect(screen.queryByRole("link", { name: "META" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "NVDA" })).toBeInTheDocument();
  });

  it("holding symbol links to stock page with account", async () => {
    mockApi(ROUTES);
    const { user } = renderPage(<PortfolioPage />, "/portfolio?account=paper");
    await user.click(await screen.findByRole("link", { name: "NVDA" }));
    expect(currentLocation()).toBe("/stock/NVDA?account=paper");
  });

  it("calendar switches month", async () => {
    const fetch = mockApi(ROUTES);
    const { user } = renderPage(<PortfolioPage />, "/portfolio");
    await user.click(await screen.findByRole("button", { name: "上个月" }));
    expect(await screen.findByText("2026 年 9 月")).toBeInTheDocument();
    const months = fetch.mock.calls
      .map(([input]) => new URL(String(input), "http://x"))
      .filter((u) => u.pathname.endsWith("/calendar"))
      .map((u) => u.searchParams.get("month"));
    expect(months).toContain("2026-09");
  });

  it("preview shows read-only notice", async () => {
    mockApi(ROUTES);
    renderPage(<PortfolioPage />, "/portfolio");
    expect(await screen.findByText(/只读预览，面板不能下单/)).toBeInTheDocument();
    expect(screen.getByText(/基于 2026-10-28 收盘信号/)).toBeInTheDocument();
    expect(screen.getByText("动量排名第 1")).toBeInTheDocument();
    expect(screen.getByText(/佣金（估算）/)).toBeInTheDocument();
  });

  it("closed trades tab shows stats", async () => {
    mockApi(ROUTES);
    const { user } = renderPage(<PortfolioPage />, "/portfolio");
    await user.click(await screen.findByRole("tab", { name: "已平仓" }));
    expect(await screen.findByText("胜率 100.0%")).toBeInTheDocument();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });

  it("sector distribution shows 数据接入中", async () => {
    mockApi(ROUTES);
    renderPage(<PortfolioPage />, "/portfolio");
    expect(await screen.findByText(/按板块分布/)).toBeInTheDocument();
    expect(screen.getAllByText("数据接入中").length).toBeGreaterThan(0);
  });
});

const INTRA = {
  session: "regular", session_text: "盘中", as_of: "2026-11-02T15:00:00Z",
  note: "Finnhub 盘中报价 · 仅供查看，交易用收盘日线", complete: true,
  market_value: 99000, day_pnl: 500,
  positions: [{ symbol: "NVDA", qty: 300, price: 201, day_pnl: 300, quote_time: "2026-11-02T14:58:00Z" }],
};

describe("portfolio intraday estimates", () => {
  it("shows estimates separately from the official NAV", async () => {
    mockApi({ ...ROUTES, "/api/market/intraday": [200, INTRA] });
    renderPage(<PortfolioPage />, "/portfolio");
    expect(await screen.findByText(/盘中持仓市值 \$99,000.00/)).toBeInTheDocument();
    expect(screen.getByText("$101,000.00")).toBeInTheDocument();
    expect(screen.getByText(/201.00 · 2026-11-02 09:58/)).toBeInTheDocument();
    expect(screen.getByText(/账户快照截至 2026-10-28/)).toBeInTheDocument();
  });
  it("withholds incomplete totals and keeps available per-position data", async () => {
    mockApi({ ...ROUTES, "/api/market/intraday": [200, { ...INTRA, complete: false, market_value: null, day_pnl: null }] });
    renderPage(<PortfolioPage />, "/portfolio");
    expect(await screen.findByText(/盘中数据不完整，暂不汇总/)).toBeInTheDocument();
    expect(screen.queryByText(/盘中持仓市值/)).toBeNull();
    expect(await screen.findByText(/201.00 · 2026-11-02 09:58/)).toBeInTheDocument();
  });
  it("does not display intraday estimates after close", async () => {
    mockApi({ ...ROUTES, "/api/market/intraday": [200, { ...INTRA, session: "post", positions: [] }] });
    renderPage(<PortfolioPage />, "/portfolio");
    await screen.findByText("$101,000.00");
    expect(screen.queryByText(/盘中持仓市值/)).toBeNull();
    expect(screen.queryByText(/201.00 ·/)).toBeNull();
  });
  it("requests quotes for the selected account", async () => {
    const fetch = mockApi({ ...withMode("live"), "/api/market/intraday": [200, INTRA] });
    renderPage(<PortfolioPage />, "/portfolio?account=live", mkMeta({ has: { live: true } }), "live");
    await screen.findByText(/盘中持仓市值/);
    const url = fetch.mock.calls.map(([u]) => new URL(String(u), "http://x")).find((u) => u.pathname === "/api/market/intraday");
    expect(url?.searchParams.get("mode")).toBe("live");
  });
});
