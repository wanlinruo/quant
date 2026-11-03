import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MarketTicker } from "./MarketTicker";

describe("MarketTicker", () => {
  it("does not create scrolling controls when the text fits", () => {
    render(<MarketTicker text="市场平稳" />);
    expect(screen.getByRole("region", { name: "市场速览" })).toHaveTextContent("市场平稳");
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("allows long summaries to be paused and stops scrolling when resized to fit", async () => {
    const user = userEvent.setup();
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(400);
    const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(160);
    render(<MarketTicker text="标普上涨，可选消费领涨，能源领跌，盘中报价持续更新。" />);
    await user.click(await screen.findByRole("button", { name: "暂停市场速览" }));
    expect(screen.getByRole("button", { name: "继续滚动市场速览" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "继续滚动市场速览" }));
    expect(screen.getByRole("button", { name: "暂停市场速览" })).toHaveAttribute("aria-pressed", "false");
    width.mockReturnValue(1000);
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(screen.queryByRole("button")).toBeNull());
  });
});
