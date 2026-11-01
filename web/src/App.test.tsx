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
});
