import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Treemap, type TreeGroup } from "./Treemap";

const groups: TreeGroup[] = [{
  key: "tech",
  label: "技术",
  cells: [{ key: "AAPL", label: "AAPL", sublabel: "+2.1%", value: 1, color: "green", title: "Apple +2.1%" }],
}];

describe("Treemap", () => {
  it("hides return text when a cell is too small", () => {
    render(<Treemap groups={groups} width={50} height={30} />);
    expect(screen.getByText("AAPL")).toHaveClass("sr");
    expect(screen.queryByText("+2.1%")).not.toBeInTheDocument();
  });

  it("shows symbol and return when a cell is large enough", () => {
    render(<Treemap groups={groups} width={100} height={100} />);
    expect(screen.getByText("AAPL")).not.toHaveClass("sr");
    expect(screen.getByText("+2.1%")).toBeInTheDocument();
  });
});
