import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { mkMeta, mockApi } from "./test/utils";

describe("App", () => {
  it("shows error panel when meta fails", async () => {
    mockApi({ "/api/meta": [500, { detail: "服务器错误" }] });
    render(<App initialPath="/" />);
    expect(await screen.findByText(/服务器错误/)).toBeInTheDocument();
  });

  it("renders shell with nav after meta loads", async () => {
    mockApi({ "/api/meta": [200, mkMeta()] });
    render(<App initialPath="/system" />);
    expect(await screen.findByRole("link", { name: "系统" })).toHaveClass("active");
  });

  it("credits TradingView Lightweight Charts in the footer", async () => {
    mockApi({ "/api/meta": [200, mkMeta()] });
    render(<App initialPath="/system" />);
    const link = await screen.findByRole("link", { name: /TradingView Lightweight Charts/ });
    expect(link).toHaveAttribute("href", "https://www.tradingview.com/");
  });

  it("shares one market request between the navigation and the home page", async () => {
    const fetch = mockApi({
      "/api/meta": [200, mkMeta()],
      "/api/market/overview": [200, {
        status: "ok", as_of: "2026-11-02", highlights: "本日市场摘要", intraday: true,
        session: "post", intraday_time: "2026-11-02T21:00:00Z",
        strip: [{ symbol: "SPY", label: "标普 500", close: 752.87, change_pct: -0.0038, spark: [] }],
      }],
    });
    render(<App initialPath="/" />);
    expect(await screen.findByText("本日市场摘要")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "市场速览" })).toHaveTextContent("本日市场摘要");
    expect(await screen.findByText("752.87")).toBeInTheDocument();
    expect(screen.getAllByTestId("market-freshness")).toHaveLength(1);
    const requests = fetch.mock.calls.filter(([u]) => new URL(String(u), "http://x").pathname === "/api/market/overview");
    expect(requests).toHaveLength(1);
  });

});
