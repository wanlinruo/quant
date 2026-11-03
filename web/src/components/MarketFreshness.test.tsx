import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { MarketOverview } from "../api/types";
import { MarketFreshness } from "./MarketFreshness";

const MARKET: MarketOverview = {
  status: "ok", as_of: "2026-11-02", intraday: true, session: "regular",
  intraday_time_start: "2026-11-02T15:00:00Z", intraday_time: "2026-11-02T15:12:00Z",
  generated_at: "2026-11-02T15:13:00Z",
};

describe("MarketFreshness", () => {
  it("distinguishes quote range from generation time", () => {
    render(<MarketFreshness market={MARKET} />);
    expect(screen.getByText(/报价 2026-11-02 10:00 至 2026-11-02 10:12 ET/)).toBeInTheDocument();
    expect(screen.getByText(/快照生成 2026-11-02 10:13 ET/)).toBeInTheDocument();
    expect(screen.getByText(/非同时报价/)).toBeInTheDocument();
  });
  it("does not advertise updating quotes after close", () => {
    render(<MarketFreshness market={{ ...MARKET, session: "post" }} />);
    expect(screen.getByText(/已收盘/)).toBeInTheDocument();
    expect(screen.getByText(/报价已停止/)).toBeInTheDocument();
    expect(screen.queryByText(/盘中轮询/)).toBeNull();
  });
  it("shows delayed quotes explicitly", () => {
    render(<MarketFreshness market={{ ...MARKET, quote_delayed: true }} />);
    expect(screen.getByText(/行情更新延迟/)).toBeInTheDocument();
  });
  it("marks prior daily data while waiting for first round", () => {
    render(<MarketFreshness market={{ ...MARKET, intraday: false, as_of: "2026-10-30" }} />);
    expect(screen.getByText(/截至 2026-10-30（美东收盘日线）/)).toBeInTheDocument();
    expect(screen.getByText(/盘中快照尚未就绪/)).toBeInTheDocument();
  });
});
