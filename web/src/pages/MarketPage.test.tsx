import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { MarketPage } from "./MarketPage";

describe("MarketPage", () => {
  it("shows pending board and my strategy card", async () => {
    mockApi({
      "/api/portfolio/paper/curve": [200, [{ date: "2026-10-28", cash: 2100, net_liquidation: 101000 }]],
      "/api/signals/latest": [200, [{ strategy: "combo", signal_date: "2026-10-28", data_as_of: "2026-10-28", target_weights: { NVDA: 0.6, META: 0.4 } }]],
      "/api/system/jobs": [200, []],
    });
    renderPage(<MarketPage />, "/");
    expect(screen.getByText("数据接入中")).toBeInTheDocument();
    expect(await screen.findByText("$101,000.00")).toBeInTheDocument();
    expect(screen.getByText(/combo · 2026-10-28 · 2 个标的/)).toBeInTheDocument();
  });
});
