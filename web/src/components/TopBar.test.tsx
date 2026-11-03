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
