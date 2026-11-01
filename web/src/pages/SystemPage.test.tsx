import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { SystemPage } from "./SystemPage";

describe("SystemPage", () => {
  it("shows kill switch read-only note and no toggle buttons", async () => {
    mockApi({
      "/api/system/flags": [200, { trading_enabled: "false" }],
      "/api/system/queue": [200, { queued: 1, running: 0, done: 3, failed: 1 }],
      "/api/system/jobs": [200, []], "/api/system/failures": [200, []], "/api/market/data-status": [200, []],
    });
    renderPage(<SystemPage />, "/system");
    expect(await screen.findByText("关闭（禁止下单）")).toBeInTheDocument();
    expect(screen.getByText("只能在服务器命令行修改：quant trading")).toBeInTheDocument();
    for (const b of screen.queryAllByRole("button")) {
      expect(b.textContent).not.toMatch(/开启|关闭/);
    }
  });

  it("shows job error first line", async () => {
    mockApi({
      "/api/system/flags": [200, {}], "/api/system/queue": [200, {}],
      "/api/system/jobs": [200, [{ job_name: "daily_signal", trade_date: "2026-10-28", status: "failed", started_at: "2026-10-28T21:00:00Z", finished_at: null, error: "数据校验未通过" }]],
      "/api/system/failures": [200, []], "/api/market/data-status": [200, []],
    });
    renderPage(<SystemPage />, "/system");
    expect(await screen.findByText("数据校验未通过")).toBeInTheDocument();
    expect(screen.getByText("未知")).toBeInTheDocument();
  });
});
