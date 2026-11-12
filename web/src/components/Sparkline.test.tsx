import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("breaks the line across missing samples without collapsing the time axis", () => {
    const { container } = render(<Sparkline values={[1, 2, null, 4, 5]} width={80} />);
    const lines = [...container.querySelectorAll("polyline")];

    expect(lines).toHaveLength(2);
    expect(lines[0]).toHaveAttribute("points", "0,22 20,16.5");
    expect(lines[1]).toHaveAttribute("points", "60,5.5 80,0");
  });

  it("shows an empty marker when fewer than two samples are available", () => {
    const { container } = render(<Sparkline values={[null, 1, null]} />);
    expect(container).toHaveTextContent("—");
    expect(container.querySelector("svg")).not.toBeInTheDocument();
  });
});
