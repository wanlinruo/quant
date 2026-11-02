import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ShellContext } from "../shell";
import { currentLocation, mkMeta, mockApi, renderAt, renderPage } from "../test/utils";
import { BacktestPage } from "./BacktestPage";
import { ANALYSIS, POSITIONS, TRADES } from "./backtest/analysisFixture";

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
      if (u.pathname === "/api/backtests/compare") expect(u.searchParams.getAll("ids").length).toBeLessThanOrEqual(5);
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

  const TIINGO = (fixedDone: number) => ({
    source: "tiingo", data_end: fixedDone ? "2026-10-28" : null,
    fixed: { done: fixedDone, total: 19 }, current: { done: 120, total: 503 }, delisted: { done: 0, total: 260 },
    unavailable: [], earnings: "none",
    quota: { hour_used: 12, hour_limit: 50, day_used: 300, day_limit: 1000, month: "2026-11", month_symbols: 139, month_limit: 500 },
  });

  it("shows backfill progress when empty", async () => {
    mockApi({ "/api/backtests": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/data-status": [200, TIINGO(0)] });
    renderPage(<BacktestPage />, "/backtest");
    expect(await screen.findByText(/补齐中/)).toBeInTheDocument();
    expect(screen.getByText(/固定标的 0\/19/)).toBeInTheDocument();
    expect(screen.getByText(/长周期结果偏乐观/)).toBeInTheDocument();
  });

  it("hides status bar in yfinance mode", async () => {
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS],
              "/api/backtests/data-status": [200, { source: "yfinance" }] });
    renderPage(<BacktestPage />, "/backtest");
    await screen.findByText("#128");
    expect(screen.queryByText(/回测数据：/)).not.toBeInTheDocument();
  });

  it("combo form hides etf fields", async () => {
    mockApi({ "/api/backtests": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/data-status": [200, TIINGO(19)] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    expect(screen.getByText("动量回看（交易日）")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox"), "combo");
    expect(screen.queryByText("动量回看（交易日）")).not.toBeInTheDocument();
  });

  it("end date input is limited to data_end", async () => {
    mockApi({ "/api/backtests": [200, []], "/api/backtests/defaults": [200, { ...DEFAULTS, end: "2026-10-28", data_end: "2026-10-28" }],
              "/api/backtests/data-status": [200, TIINGO(19)] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    expect(screen.getByLabelText("结束")).toHaveAttribute("max", "2026-10-28");
  });

  it("start date input is limited to min_start", async () => {
    mockApi({ "/api/backtests": [200, []], "/api/backtests/defaults": [200, { ...DEFAULTS, data_end: "2026-10-28", min_start: "2014-07-01" }],
              "/api/backtests/data-status": [200, TIINGO(19)] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    expect(screen.getByLabelText("开始")).toHaveAttribute("min", "2014-07-01");
  });

  it("shows coverage warning on run", async () => {
    const r = { ...run(130), metrics: { ...run(130).metrics, data: { source: "tiingo", data_end: "2026-10-28", coverage: 0.82, missing_by_year: {}, earnings: "none" } } };
    mockApi({ "/api/backtests": [200, [r]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS],
              "/api/backtests/data-status": [200, TIINGO(19)] });
    renderPage(<BacktestPage />, "/backtest");
    expect(await screen.findByText("数据覆盖 82%")).toBeInTheDocument();
    expect(screen.getByText("历史财报日缺失")).toBeInTheDocument();
  });

  it("filters runs and shows failure reason", async () => {
    const failed = { ...run(126, "failed"), metrics: {}, error: "回测数据尚未补齐：缺 QQQ" };
    mockApi({ "/api/backtests": [200, [run(128), failed]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await screen.findByText("#128");
    await user.click(screen.getByRole("button", { name: "失败" }));
    expect(screen.queryByText("#128")).not.toBeInTheDocument();
    expect(screen.getByText("回测数据尚未补齐：缺 QQQ")).toBeInTheDocument();
  });

  it("stat bar shows qqq comparison from analysis", async () => {
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS] });
    renderPage(<BacktestPage />, "/backtest");
    const bar = await screen.findByTestId("statbar");
    expect(await within(bar).findByText("12.30%")).toBeInTheDocument();
    expect(within(bar).getByText("QQQ 10.10%")).toBeInTheDocument();
    expect(within(bar).getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/初始资金 \$100,000/)).toBeInTheDocument();
  });

  it("main chart stays mounted across tabs and pending tabs say so", async () => {
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS],
      "/api/backtests/128/robustness": [200, { status: "not_requested", note: "未运行：新建回测时勾选“同时跑稳健性检验”" }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    const chart = await screen.findByTestId("main-chart");
    await user.click(screen.getByRole("button", { name: "收益分析" }));
    await user.click(screen.getByRole("button", { name: "交易明细" }));
    await user.click(screen.getByRole("button", { name: "稳健性" }));
    expect(await screen.findByText(/未运行：新建回测时勾选/)).toBeInTheDocument();
    expect(screen.queryByTestId("rb-grid")).toBeNull();
    expect(screen.getByTestId("main-chart")).toBe(chart);
  });

  it("copy params opens drawer with strategy and dates", async () => {
    const r = { ...run(128), strategy: "etf_momentum", params: { lookback: 99, top_k: 2 }, start: "2018-01-02", end: "2024-12-31" };
    mockApi({ "/api/backtests": [200, [r]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, { ...ANALYSIS, strategy: "etf_momentum" }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "复制参数新建" }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByDisplayValue("2018-01-02")).toBeInTheDocument();
    expect(within(dlg).getByDisplayValue("99")).toBeInTheDocument();
  });

  async function openTab(name: string) {
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS], "/api/backtests/128/trades": [200, TRADES], "/api/backtests/128/positions": [200, POSITIONS] });
    const r = renderPage(<BacktestPage />, "/backtest");
    await screen.findByText("QQQ 10.10%");
    await r.user.click(screen.getByRole("button", { name }));
    return screen.getByTestId(`tab-${name}`);
  }

  it("overview tab", async () => {
    const tab = await openTab("概览");
    expect(within(tab).getByText("待稳健性检验（#68）")).toBeInTheDocument();
    expect(within(tab).getAllByText("通过").length).toBe(3);
    expect(within(tab).getByText("未通过")).toBeInTheDocument();
    expect(within(tab).getByText("样本外（2023 起）")).toBeInTheDocument();
    expect(within(tab).getByText("15.00%")).toBeInTheDocument();
    expect(within(tab).getByText("2023")).toBeInTheDocument();
  });

  it("returns tab", async () => {
    const tab = await openTab("收益分析");
    const cell = within(tab).getByText("+5.0%");
    expect(cell.closest("td")).toHaveClass("up");
    expect(within(tab).getByText("2023-01-31")).toBeInTheDocument();
  });

  it("risk tab", async () => {
    const tab = await openTab("回撤与风险");
    expect(within(tab).getByText("-21.30%")).toBeInTheDocument();
    expect(within(tab).getByText("82%")).toBeInTheDocument();
    expect(within(tab).getByText("VaR 95%（日）")).toBeInTheDocument();
  });

  it("params tab", async () => {
    const tab = await openTab("参数与日志");
    expect(within(tab).getByText("top_n").closest("tr")).toHaveClass("changed");
    expect(within(tab).getByText("sma").closest("tr")).not.toHaveClass("changed");
    expect(within(tab).getByText(/--param top_n=12/)).toBeInTheDocument();
    expect(within(tab).getByText("abc1234")).toBeInTheDocument();
    expect(within(tab).getByText(/开始回测/)).toBeInTheDocument();
    await userEvent.click(within(tab).getByRole("checkbox", { name: "只看警告" }));
    expect(within(tab).queryByText(/开始回测/)).not.toBeInTheDocument();
    expect(within(tab).getByText(/NVDA 数据中断/)).toBeInTheDocument();
  });

  it("compare button disabled in single view and bench note shown", async () => {
    const note = "QQQ 数据只覆盖 2019-01-02 – 2026-10-30，与回测区间不一致，未作对照";
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, { ...ANALYSIS, bench_note: note }] });
    renderPage(<BacktestPage />, "/backtest");
    await screen.findByTestId("statbar");
    const btn = screen.getByRole("button", { name: "加入对比" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "在左侧再勾选一个回测即可对比");
    expect(await screen.findByText(note)).toBeInTheDocument();
    expect(screen.getByText("#128 · combo")).toBeInTheDocument();
  });

  it("trades tab", async () => {
    const tab = await openTab("交易明细");
    expect(await within(tab).findByText("50.0%")).toBeInTheDocument();
    expect(within(tab).getByText("4.00")).toBeInTheDocument();
    const table = within(tab).getByTestId("trade-table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("持有中")).toBeInTheDocument();
    const r = within(table).getByText("BBB").closest("tr")!;
    await userEvent.click(r);
    expect(r).toHaveAttribute("aria-selected", "true");
    await userEvent.click(within(tab).getByRole("button", { name: "波段仓" }));
    expect(within(table).queryByText("AAA")).not.toBeInTheDocument();
    expect(within(tab).getByTestId("scatter").querySelectorAll("circle")).toHaveLength(1); // 散点跟随筛选
    expect(within(tab).getAllByText("动量排名跌出前 10").length).toBeGreaterThan(0);
  });

  it("positions tab", async () => {
    const tab = await openTab("持仓变化");
    expect(await within(tab).findByTestId("stack")).toBeInTheDocument();
    expect(within(tab).getByTestId("stack").querySelectorAll("rect.timing")).toHaveLength(1);
    expect(within(tab).getByText("25.0%")).toBeInTheDocument();
    expect(within(tab).getByText("120 天")).toBeInTheDocument();
  });

  it("returns and risk show sleeves and timing", async () => {
    const ret = await openTab("收益分析");
    expect(within(ret).queryByText(/子仓贡献.*B 批接入/)).not.toBeInTheDocument();
    expect(within(ret).getByText("子仓累计收益贡献")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "回撤与风险" }));
    const risk = screen.getByTestId("tab-回撤与风险");
    expect(within(risk).getByText("+3.00%")).toBeInTheDocument();
  });

  it("old run shows note in trades tab", async () => {
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS], "/api/backtests/128/trades": [200, { note: "此回测运行于交易记录功能上线之前，重新运行（复制参数新建）即可查看", trades: [], by_reason: [], by_symbol: [] }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await screen.findByText("QQQ 10.10%");
    await user.click(screen.getByRole("button", { name: "交易明细" }));
    expect(await screen.findByText(/重新运行/)).toBeInTheDocument();
  });

  it("tab from url", async () => {
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS], "/api/backtests/128/trades": [200, TRADES] });
    renderPage(<BacktestPage />, "/backtest?tab=trades");
    expect(await screen.findByTestId("trade-table")).toBeInTheDocument();
  });

  it("selected trade expands adjustments and resets on run switch", async () => {
    mockApi({ "/api/backtests": [200, [run(128), run(127)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS], "/api/backtests/128/trades": [200, TRADES], "/api/backtests/127/analysis": [200, { ...ANALYSIS, id: 127 }], "/api/backtests/127/trades": [200, TRADES] });
    const { user } = renderPage(<BacktestPage />, "/backtest/128?tab=trades");
    const table = await screen.findByTestId("trade-table");
    const row = within(table).getByText("AAA").closest("tr")!;
    await user.click(row);
    expect(row).toHaveAttribute("aria-selected", "true");
    expect(within(table).getByText("2022-12-10")).toBeInTheDocument();
    expect(within(table).getByText("+20 股")).toBeInTheDocument();
    await user.click(screen.getByText("#127"));
    const t2 = await screen.findByTestId("trade-table");
    expect(within(t2).getAllByRole("row").some((r) => r.getAttribute("aria-selected") === "true")).toBe(false);
  });

  const COMPARE = {
    note: null, start: "2019-01-02", end: "2024-12-31", skipped: [126],
    runs: [{ id: 128, strategy: "combo", start: "2016-10-28", end: "2026-10-29" }, { id: 127, strategy: "combo", start: "2019-01-02", end: "2024-12-31" }],
    dates: ["2019-01-02", "2019-01-03"], series: { "128": [1, 1.01], "127": [1, 0.99] }, drawdown: { "128": [0, 0], "127": [0, -0.01] },
    metrics: [
      { key: "cagr", label: "年化", kind: "pct", scope: "common", values: { "128": 0.12, "127": 0.08 }, best: 128 },
      { key: "max_drawdown", label: "最大回撤", kind: "pct", scope: "common", values: { "128": -0.3, "127": -0.2 }, best: 127 },
      { key: "trades", label: "交易笔数", kind: "int", scope: "full", values: { "128": 37, "127": null }, best: null },
    ],
    params: [{ key: "top_n", values: { "128": 10, "127": 12 }, differs: true }, { key: "sma", values: { "128": 120, "127": 120 }, differs: false }],
    corr: [[1, 0.62], [0.62, 1]],
  };

  it("compare view", async () => {
    mockApi({ "/api/backtests": [200, [run(128), run(127)]], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/compare": [200, COMPARE] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    const boxes = await screen.findAllByRole("checkbox");
    await user.click(boxes[1]);
    const view = await screen.findByTestId("compare");
    expect(await within(view).findByText(/共同区间 2019-01-02 → 2024-12-31/)).toBeInTheDocument();
    expect(within(view).getByText(/已跳过未完成的回测：#126/)).toBeInTheDocument();
    expect(within(view).getByText("12.00%").closest("td")).toHaveClass("best");
    expect(within(view).getByText("-20.00%").closest("td")).toHaveClass("best");
    expect(within(view).getByText("top_n").closest("tr")).toHaveClass("changed");
    expect(within(view).getByText("sma").closest("tr")).not.toHaveClass("changed");
    expect(within(view).getAllByText("0.62")).toHaveLength(2);
    expect(within(view).getByText("（全区间）")).toBeInTheDocument();
  });

  it("compare view shows note when no overlap", async () => {
    mockApi({ "/api/backtests": [200, [run(128), run(127)]], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/compare": [200, { note: "所选回测没有共同区间（至少需要 20 个交易日重叠）", skipped: [], runs: [], dates: [], series: {}, drawdown: {}, metrics: [], params: [], corr: [] }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click((await screen.findAllByRole("checkbox"))[1]);
    expect(await screen.findByText(/没有共同区间/)).toBeInTheDocument();
  });

  const SCHEMA = {
    combo: [
      { key: "momentum_weight", group: null, type: "float", default: 0.4 },
      { key: "top_n", group: "momentum", type: "int", default: 4 },
      { key: "stop_pct", group: "swing", type: "float", default: 0.07 },
      { key: "stocks", group: "swing", type: "list", default: ["AAPL", "NVDA"] },
      { key: "use_inverse", group: "swing", type: "bool", default: false },
    ],
    etf_momentum: [],
  };

  it("drawer generates combo params and submits only changes", async () => {
    const fetch = mockApi({ "/api/backtests": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/param-schema": [200, SCHEMA], "POST /api/backtests": [201, { id: 9 }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    await user.selectOptions(screen.getByRole("combobox"), "combo");
    const dlg = screen.getByRole("dialog");
    expect(await within(dlg).findByText("波段仓（swing）")).toBeInTheDocument();
    expect(within(dlg).getByText(/默认值与服务器配置一致/)).toBeInTheDocument();
    const stop = within(dlg).getByLabelText("swing.stop_pct");
    await user.clear(stop);
    await user.type(stop, "0.05");
    await user.click(within(dlg).getByRole("button", { name: "提交到队列" }));
    const post = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    const body = JSON.parse(String((post![1] as RequestInit).body));
    expect(body.strategy).toBe("combo");
    expect(body.params).toEqual({ swing: { stop_pct: 0.05 } });
  });

  it("drawer blocks empty number fields", async () => {
    const fetch = mockApi({ "/api/backtests": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/param-schema": [200, SCHEMA], "POST /api/backtests": [201, { id: 9 }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    await user.click(await screen.findByRole("button", { name: "+ 新建回测" }));
    await user.selectOptions(screen.getByRole("combobox"), "combo");
    const dlg = screen.getByRole("dialog");
    await user.clear(await within(dlg).findByLabelText("momentum.top_n"));
    await user.click(within(dlg).getByRole("button", { name: "提交到队列" }));
    expect(await within(dlg).findByText(/momentum.top_n 须为数字/)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("robustness tab shows heatmap, neighbors, walk-forward, costs and starts", async () => {
    const m = (cagr: number, sharpe: number, max_drawdown: number) => ({ cagr, sharpe, max_drawdown });
    const cells = [0, 1, 2].flatMap((y) => [0, 1, 2].map((x) => ({
      x, y, label: `c${x}${y}`, run_id: 200 + x + 3 * y, status: x === 2 && y === 0 ? "skipped" : "done",
      metrics: x === 2 && y === 0 ? null : m(0.1 + x * 0.02, 1 + x * 0.1, -0.2), center: x === 1 && y === 1,
    })));
    const ROB = {
      status: "pending", note: "", progress: { total: 17, done: 15, failed: 1, queued: 1, running: 0 },
      grid: { x_label: "动量仓比例", y_label: "择时波动阈值", x_values: ["30%", "40%", "50%"], y_values: ["25%", "30%", "35%"], cells },
      neighbors: [{ label: "动量仓比例 30% · 择时波动阈值 30%", run_id: 203, status: "done", metrics: m(0.1, 0.9, -0.2), passed: false, base: false },
        { label: "动量只数 5", run_id: 210, status: "failed", metrics: null, passed: null, base: false }],
      neighbors_verdict: null, neighbors_note: "不完整：1 项未完成或失败",
      walk_forward: { train_years: 3, note: "每年用前 3 年夏普最高的参数", segments: [{ year: 2019, chosen: "c001", test_return: 0.12, base_return: 0.1 }],
        strategy: m(0.11, 1.0, -0.2), base: m(0.1, 1.1, -0.18) },
      costs: [{ label: "成本 ×0", run_id: 211, status: "done", metrics: m(0.2, 1.4, -0.2), passed: null, base: false },
        { label: "成本 ×1（本次）", run_id: 128, status: "done", metrics: m(0.18, 1.3, -0.2), passed: null, base: true }],
      starts: [{ label: "起点 2016-10-28（本次）", run_id: 128, status: "done", metrics: m(0.18, 1.3, -0.2), passed: null, base: true },
        { label: "起点 2017-01-28", run_id: 214, status: "queued", metrics: null, passed: null, base: false }],
    };
    mockApi({ "/api/backtests": [200, [run(128)]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/128/analysis": [200, ANALYSIS], "/api/backtests/128/robustness": [200, ROB] });
    renderPage(<BacktestPage />, "/backtest?tab=robust");
    const grid = await screen.findByTestId("rb-grid");
    expect(grid.querySelectorAll(".rb-cell")).toHaveLength(9);
    expect(grid.querySelector(".rb-cell.center")).toHaveTextContent("1.10");
    expect(grid.querySelector(".rb-cell.skipped")).toHaveTextContent("—");
    expect(screen.getByText(/15 \/ 17/)).toBeInTheDocument();
    expect(screen.getByText("不完整：1 项未完成或失败")).toBeInTheDocument();
    expect(screen.getByText("动量仓比例 30% · 择时波动阈值 30%").closest("tr")).toHaveTextContent("未通过");
    expect(screen.getByText("2019").closest("tr")).toHaveTextContent("c001");
    expect(screen.getByText("成本 ×1（本次）")).toBeInTheDocument();
    expect(screen.getByText("起点 2017-01-28").closest("tr")).toHaveTextContent("排队");
  });

  it("drawer can request robustness and list shows progress", async () => {
    const parent = { ...run(140), robust: { total: 17, done: 3, failed: 0, queued: 14, running: 0 } };
    const fetch = mockApi({ "/api/backtests": [200, [parent]], "/api/backtests/equity": [200, []], "/api/backtests/defaults": [200, DEFAULTS], "/api/backtests/140/analysis": [200, ANALYSIS], "POST /api/backtests": [201, { id: 141 }] });
    const { user } = renderPage(<BacktestPage />, "/backtest");
    expect(await screen.findByText(/稳健 3\/17/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "+ 新建回测" }));
    await user.click(await screen.findByLabelText(/同时跑稳健性检验/));
    await user.click(screen.getByRole("button", { name: "提交到队列" }));
    const post = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post![1] as RequestInit).body)).robustness).toBe(true);
  });
});
