import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { StockPage } from "./StockPage";

describe("StockPage", () => {
  it("shows symbol and empty state without bars", async () => {
    mockApi({ "/api/market/bars/NVDA": [200, []] });
    renderPage(<Routes><Route path="/stock/:symbol" element={<StockPage />} /></Routes>, "/stock/NVDA");
    expect(screen.getByRole("heading", { name: "NVDA" })).toBeInTheDocument();
    expect(await screen.findByText("暂无数据")).toBeInTheDocument();
  });

  it("shows last close and change", async () => {
    mockApi({ "/api/market/bars/NVDA": [200, [
      { date: "2026-10-29", open: 1, high: 1, low: 1, close: 100, adj_close: 100, volume: 1 },
      { date: "2026-10-30", open: 1, high: 1, low: 1, close: 103.4, adj_close: 103.4, volume: 1 },
    ]] });
    renderPage(<Routes><Route path="/stock/:symbol" element={<StockPage />} /></Routes>, "/stock/NVDA");
    expect(await screen.findByText("103.40")).toBeInTheDocument();
    expect(screen.getByText("+3.40%")).toBeInTheDocument();
  });
});
