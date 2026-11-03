import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { MarketOverview } from "../api/types";
import { MarketFreshness } from "./MarketFreshness";

const MARKET: MarketOverview = {
  status: "ok", as_of: "2026-11-02", intraday: true, session: "regular",
  intraday_time_start: "2026-11-02T15:00:00Z", intraday_time: "2026-11-02T15:12:00Z",
  generated_at: "2026-11-02T15:13:00Z",
};

describe("MarketFreshness", () => {
  it("keeps only status and the latest quote visible until details are requested", async () => {
    const user = userEvent.setup();
    render(<MarketFreshness market={MARKET} />);
    expect(screen.getByRole("status")).toHaveTextContent("盘中");
    expect(screen.getByText("行情 11-02 10:12 ET")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText(/非同时报价/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "查看数据说明" }));
    expect(screen.getByRole("dialog", { name: "数据与更新" })).toBeInTheDocument();
    expect(screen.getByText("2026-11-02 10:00–10:12 ET")).toBeInTheDocument();
    expect(screen.getByText("2026-11-02 10:13 ET")).toBeInTheDocument();
    expect(screen.getByText(/非同时报价/)).toBeInTheDocument();
  });

  it("returns keyboard focus to the trigger on Escape", async () => {
    const user = userEvent.setup();
    render(<MarketFreshness market={MARKET} />);
    const trigger = screen.getByRole("button", { name: "查看数据说明" });
    await user.click(trigger);
    expect(screen.getByRole("button", { name: "关闭数据说明" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on an outside click and when focus moves outside", async () => {
    const user = userEvent.setup();
    render(<><MarketFreshness market={MARKET} /><button>其他区块</button></>);
    await user.click(screen.getByRole("button", { name: "查看数据说明" }));
    await user.click(screen.getByRole("button", { name: "其他区块" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "查看数据说明" }));
    fireEvent.focusIn(screen.getByRole("button", { name: "其他区块" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the closed state without advertising an active refresh", async () => {
    const user = userEvent.setup();
    render(<MarketFreshness market={{ ...MARKET, session: "post" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("已收盘");
    await user.click(screen.getByRole("button", { name: "查看数据说明" }));
    expect(screen.getByText(/报价已停止/)).toBeInTheDocument();
  });

  it("keeps delayed data visible without opening the popover", () => {
    render(<MarketFreshness market={{ ...MARKET, quote_delayed: true }} />);
    expect(screen.getByRole("status")).toHaveTextContent("更新延迟");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("marks prior daily data while waiting for the first round", async () => {
    const user = userEvent.setup();
    render(<MarketFreshness market={{ ...MARKET, intraday: false, as_of: "2026-10-30" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("采集中");
    expect(screen.getByText("行情 10-30 收盘")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看数据说明" }));
    expect(screen.getByText(/盘中快照尚未就绪/)).toBeInTheDocument();
  });
});
