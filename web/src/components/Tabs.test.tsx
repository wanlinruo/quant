import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Tabs } from "./Tabs";

const OPTIONS = [
  { key: "one", label: "一" },
  { key: "two", label: "二" },
  { key: "three", label: "三" },
] as const;

function Example() {
  const [value, setValue] = useState<(typeof OPTIONS)[number]["key"]>("one");
  return <Tabs tabs={[...OPTIONS]} value={value} onChange={setValue} />;
}

describe("Tabs", () => {
  it("exposes tab semantics and a single tab stop", () => {
    render(<Example />);
    const tabs = screen.getAllByRole("tab");
    expect(screen.getByRole("tablist", { name: "选项卡" })).toBeInTheDocument();
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");
  });

  it("supports arrow, Home, and End keyboard navigation", async () => {
    const user = userEvent.setup();
    render(<Example />);
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();

    await user.keyboard("{ArrowLeft}");
    expect(tabs[2]).toHaveFocus();
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(tabs[0]).toHaveFocus();
    await user.keyboard("{End}");
    expect(tabs[2]).toHaveFocus();
  });
});
