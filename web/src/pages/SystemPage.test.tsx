import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { SystemPage } from "./SystemPage";

const dates = ["2026-10-26", "2026-10-27", "2026-10-28"];
const cell = (date: string, status: string, attempts = 1) => ({ date, status, attempts, duration_s: 40 });
const SYS = {
  mode: "paper", today: "2026-10-28",
  banner: { level: "error", items: [
    { level: "error", text: "daily_signal 2026-10-28 失败：数据校验未通过", command: "quant job daily_signal --date 2026-10-28 --rerun-failed" },
    { level: "warn", text: "熔断开关关闭：不会下单（服务器上 quant trading enable --yes 开启）", command: null },
  ] },
  cards: [
    { key: "mode", label: "交易模式", value: "模拟盘 PAPER", sub: "Broker：tiger", level: "info" },
    { key: "kill_switch", label: "熔断开关", value: "关闭（禁止下单）", sub: "修改于 10-27 09:00", level: "warn" },
    { key: "broker", label: "券商连接", value: "10-28 09:51 成功", sub: "", level: "ok" },
    { key: "reconcile", label: "持仓对账", value: "已对账", sub: "2026-10-28", level: "ok" },
    { key: "today_jobs", label: "今日任务", value: "1 / 7", sub: "", level: "warn" },
    { key: "data", label: "数据完整度", value: "4 / 5", sub: "", level: "warn" },
  ],
  grid: { dates, jobs: [
    { job: "daily_data", cells: [cell(dates[0], "na", 0), cell(dates[1], "retry_success", 2), cell(dates[2], "success")], success_rate: 1, avg_s: 40 },
    { job: "daily_signal", cells: [cell(dates[0], "na", 0), cell(dates[1], "success"), cell(dates[2], "failed")], success_rate: 0.5, avg_s: 12 },
  ] },
  timeline: [
    { job: "daily_execute", planned_at: "2026-10-28T09:50:00-04:00", status: "missing", duration_s: null, error: null },
    { job: "daily_signal", planned_at: "2026-10-28T17:00:00-04:00", status: "failed", duration_s: 12, error: "数据校验未通过" },
  ],
  sources: [{ key: "market", label: "首页市场（Massive）", status: "ok", updated: "2026-10-29T00:40:00Z", detail: "截至 2026-10-28，覆盖 100%", progress: null },
            { key: "news", label: "新闻（Finnhub）", status: "stale", updated: "2026-10-20T01:00:00Z", detail: "", progress: null }],
  risk: [{ label: "当日换手", used: 0.35, limit: 2.2, kind: "pct", text: "" }, { label: "近 30 天被拒订单", used: 1, limit: null, kind: "int", text: "" },
         { label: "标的范围", used: null, limit: null, kind: "text", text: "白名单 + 标普 500 成分" }],
  upcoming: [{ date: "2026-10-30", weekday: "周五", tags: ["月末调仓"] }, { date: "2026-11-26", weekday: "周四", tags: ["休市"] }],
  events: [{ time: "2026-10-28T21:00:00Z", level: "error", text: "daily_signal 2026-10-28 第 1 次失败：数据校验未通过" }],
  queue: { queued: 1, running: 1, done: 3, failed: 1, current_id: 7, current_strategy: "combo", current_started: "2026-10-28T20:00:00Z", last_finished: "2026-10-28T19:00:00Z" },
};
const BASE = { "/api/system/overview": [200, SYS], "/api/market/data-status": [200, []] } as const;

describe("SystemPage", () => {
  it("banner shows error with command and kill switch stays read-only", async () => {
    mockApi(BASE);
    renderPage(<SystemPage />, "/system");
    const banner = await screen.findByTestId("banner");
    expect(within(banner).getByText("quant job daily_signal --date 2026-10-28 --rerun-failed")).toBeInTheDocument();
    expect(banner).toHaveClass("error");
    expect(screen.getByText("关闭（禁止下单）")).toBeInTheDocument();
    expect(screen.getByText(/只能在服务器命令行修改/)).toBeInTheDocument();
    for (const b of screen.queryAllByRole("button")) expect(b.textContent).not.toMatch(/开启|关闭/);
  });

  it("job grid and attempt detail", async () => {
    mockApi({ ...BASE, "/api/system/job-runs": [200, [
      { attempt: 1, started_at: "2026-10-27T19:31:00Z", finished_at: "2026-10-27T20:31:00Z", duration_s: 3600, result: "failed", error: "超时" },
      { attempt: 2, started_at: "2026-10-27T20:31:00Z", finished_at: "2026-10-27T20:31:40Z", duration_s: 40, result: "success", error: null },
    ]] });
    const { user } = renderPage(<SystemPage />, "/system");
    const grid = await screen.findByTestId("job-grid");
    expect(grid.querySelectorAll(".jc")).toHaveLength(6);
    expect(grid.querySelectorAll(".jc.retry_success")).toHaveLength(1);
    expect(within(grid).getByText("50%")).toBeInTheDocument();
    await user.click(grid.querySelector(".jc.retry_success") as HTMLElement);
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByText("超时")).toBeInTheDocument();
    expect(within(dlg).getByText(/daily_data · 2026-10-27/)).toBeInTheDocument();
  });

  it("today timeline, sources, risk, upcoming, events, queue and pending resources", async () => {
    mockApi(BASE);
    renderPage(<SystemPage />, "/system");
    const tl = await screen.findByTestId("timeline");
    expect(within(tl).getByText("09:50")).toBeInTheDocument();
    expect(within(tl).getByText("数据校验未通过")).toBeInTheDocument();
    expect(screen.getByText("新闻（Finnhub）").closest("tr")).toHaveTextContent("过期");
    expect(screen.getByText("35.0% / 220.0%")).toBeInTheDocument();
    expect(screen.getByText("白名单 + 标普 500 成分")).toBeInTheDocument();
    expect(screen.getByText("月末调仓")).toBeInTheDocument();
    expect(screen.getByText(/第 1 次失败/)).toBeInTheDocument();
    expect(screen.getByText(/#7 combo/)).toBeInTheDocument();
    expect(screen.getAllByText(/资源采集接入中/).length).toBeGreaterThanOrEqual(3);
  });
});
