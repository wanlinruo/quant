import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { currentLocation, mkMeta, renderAt } from "../test/utils";
import { TopBar } from "./TopBar";

describe("TopBar", () => {
  it("shows four nav items", () => {
    renderAt("/", <TopBar meta={mkMeta()} account="paper" onAccountChange={vi.fn()} />);
    for (const name of ["市场", "组合", "回测", "系统"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("shows live stripe and title prefix", () => {
    const meta = mkMeta({ has: { live: true } });
    renderAt("/portfolio", <TopBar meta={meta} account="live" onAccountChange={vi.fn()} />);
    expect(screen.getByTestId("live-stripe")).toBeInTheDocument();
    expect(document.title.startsWith("[实盘] ")).toBe(true);
  });

  it("has no live stripe for paper", () => {
    renderAt("/portfolio", <TopBar meta={mkMeta()} account="paper" onAccountChange={vi.fn()} />);
    expect(screen.queryByTestId("live-stripe")).toBeNull();
    expect(document.title.startsWith("[实盘]")).toBe(false);
  });

  it("formats data freshness in ET and Beijing", () => {
    const meta = mkMeta({ data_as_of: "2026-10-31T00:15:00Z" });
    renderAt("/", <TopBar meta={meta} account="paper" onAccountChange={vi.fn()} />);
    expect(
      screen.getByText("2026-10-30 周五 · 最近任务完成 20:15 ET（北京 08:15）"),
    ).toBeInTheDocument();
  });

  it("shows no data when freshness unknown", () => {
    renderAt("/", <TopBar meta={mkMeta()} account="paper" onAccountChange={vi.fn()} />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });

  it("search jumps to stock page and keeps the account", async () => {
    const { user } = renderAt(
      "/?account=live",
      <TopBar meta={mkMeta({ has: { live: true } })} account="live" onAccountChange={vi.fn()} />,
    );
    await user.type(screen.getByRole("searchbox"), "nvda{Enter}");
    expect(currentLocation()).toBe("/stock/NVDA?account=live");
  });
});

const MARKET = {
  status: "ok" as const, highlights: "标普 500上涨，科技股领涨", as_of: "2026-11-02",
  intraday: true, session: "post" as const, intraday_time: "2026-11-02T21:00:00Z",
};

describe("TopBar market information", () => {
  it("places the summary and quote status in the navigation, keeping job time in the popover", async () => {
    const meta = mkMeta({ data_as_of: "2026-11-03T02:29:00Z" });
    const { user } = renderAt("/", <TopBar meta={meta} market={MARKET} account="paper" onAccountChange={vi.fn()} />);
    expect(screen.getByRole("region", { name: "市场速览" })).toHaveTextContent(MARKET.highlights);
    expect(screen.getByRole("status")).toHaveTextContent("已收盘");
    expect(screen.getByText("行情 11-02 16:00 ET")).toBeInTheDocument();
    expect(screen.queryByText(/最近任务完成/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "查看数据说明" }));
    expect(screen.getByText("最近任务")).toBeInTheDocument();
    expect(screen.getByText("2026-11-02 21:29 ET")).toBeInTheDocument();
  });
  it("keeps the delayed status visible in the navigation", () => {
    renderAt("/", <TopBar meta={mkMeta()} market={{ ...MARKET, quote_delayed: true }} account="paper" onAccountChange={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("更新延迟");
  });
});
