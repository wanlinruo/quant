import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mkMeta } from "../test/utils";
import { AccountSwitch } from "./AccountSwitch";

describe("AccountSwitch", () => {
  it("disables accounts without data", () => {
    const meta = mkMeta();
    render(<AccountSwitch meta={meta} value="paper" onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "实盘" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "实盘" })).toHaveAttribute("title", "暂无数据");
    expect(screen.getByRole("button", { name: "模拟盘" })).toHaveAttribute("aria-pressed", "true");
  });

  it("hides local sim unless trading mode is sim", () => {
    render(<AccountSwitch meta={mkMeta()} value="paper" onChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "本地模拟" })).toBeNull();
  });

  it("shows local sim when trading mode is sim", () => {
    const meta = mkMeta({ trading_mode: "sim", has: { sim: true } });
    render(<AccountSwitch meta={meta} value="sim" onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "本地模拟" })).toBeEnabled();
  });

  it("calls onChange", async () => {
    const onChange = vi.fn();
    render(<AccountSwitch meta={mkMeta({ has: { live: true } })} value="paper" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "实盘" }));
    expect(onChange).toHaveBeenCalledWith("live");
  });
});
