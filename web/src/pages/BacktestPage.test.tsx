import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { ShellContext } from "../shell";
import { currentLocation, mkMeta, mockApi, renderAt, renderPage } from "../test/utils";
import { BacktestPage } from "./BacktestPage";

const run = (id: number, status = "done") => ({
  id, strategy: "combo", status, start: "2016-10-28", end: "2026-10-29", params: {}, costs: null,
  metrics: { total_return: 1.5, cagr: 0.248, volatility: 0.19, max_drawdown: -0.213, sharpe: 1.21, calmar: 1.16, turnover: 4.6, trades: 1284 },
  error: "", created_at: "2026-10-29T21:14:00Z", finished_at: "2026-10-29T21:17:00Z",
});
const DEFAULTS = {
  strategies: ["combo", "etf_momentum"], universe_options: ["SPY", "QQQ", "TLT"], universe: ["SPY", "QQQ"],
  safe_asset: "SHY", lookback: 126, top_k: 1, sma_window: 200, cash: "100000",
  commission_per_share: "0.0039", min_commission: "0.99", slippage: "0.0005", start: "2016-10-01", end: "2026-10-30",
};

describe("BacktestPage", () => {
  it("lists runs and shows metrics of the selected run", async () => {
    mockApi({ "/api/backtests": [200, [run(128), run(127)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS] });
    renderPage(<BacktestPage />, "/backtest");
    expect(await screen.findByText("#128")).toBeInTheDocument();
    expect(screen.getAllByText("24.80%").length).toBeGreaterThan(0);
  });

  it("limits comparison to five runs", async () => {
    mockApi({ "/api/backtests": [200, [1, 2, 3, 4, 5, 6].map((i) => run(i))], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    const boxes = await screen.findAllByRole("checkbox");
    for (const b of boxes.slice(0, 5)) if (!(b as HTMLInputElement).checked) await user.click(b);
    expect(boxes[5]).toBeDisabled();
  });

  it("submits backtest and shows errors from 422", async () => {
    mockApi({
      "/api/backtests": [200, []], "/api/backtests/defaults": [200, DEFAULTS],
      "POST /api/backtests": [422, { detail: ["请填写佣金（老虎实际费率待确认，见 issue #5）"] }],
    });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    await user.click(await screen.findByRole("button", { name: "提交到队列" }));
    expect(await screen.findByText("请填写佣金（老虎实际费率待确认，见 issue #5）")).toBeInTheDocument();
  });

  it("shows success after submit", async () => {
    mockApi({
      "/api/backtests": [200, []], "/api/backtests/defaults": [200, DEFAULTS],
      "POST /api/backtests": [201, { id: 131 }],
    });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    await user.click(await screen.findByRole("button", { name: "提交到队列" }));
    expect(await screen.findByText(/已提交回测 #131/)).toBeInTheDocument();
  });

  it("clicking a row never puts more than five runs into comparison", async () => {
    const fetch = mockApi({ "/api/backtests": [200, [1, 2, 3, 4, 5, 6].map((i) => run(i))], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    const boxes = await screen.findAllByRole("checkbox");
    for (const b of boxes.slice(0, 5)) if (!(b as HTMLInputElement).checked) await user.click(b);
    await user.click(screen.getByText("#6"));
    expect(boxes.filter((b) => (b as HTMLInputElement).checked)).toHaveLength(5);
    expect(boxes[5]).toBeChecked();
    for (const [input] of fetch.mock.calls) {
      const u = new URL(String(input), "http://x");
      if (u.pathname === "/api/backtests/equity") expect(u.searchParams.getAll("ids").length).toBeLessThanOrEqual(5);
    }
  });

  function renderRoute(path: string) {
    return renderAt(
      path,
      <ShellContext.Provider value={{ meta: mkMeta(), account: "paper" }}>
        <Routes>
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/backtest/:id" element={<BacktestPage />} />
        </Routes>
      </ShellContext.Provider>,
    );
  }

  it("opens the run named in /backtest/:id", async () => {
    mockApi({ "/api/backtests": [200, [run(128), run(127)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS] });
    renderRoute("/backtest/127");
    expect(await screen.findByText("#127 · combo")).toBeInTheDocument();
  });

  it("clicking a row updates the url and keeps the account", async () => {
    mockApi({ "/api/backtests": [200, [run(128), run(127)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS] });
    const { user } = renderRoute("/backtest?account=paper");
    await user.click(await screen.findByText("#127"));
    expect(currentLocation()).toBe("/backtest/127?account=paper");
    expect(screen.getByText("#127 · combo")).toBeInTheDocument();
  });
});
