import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable } from "./DataTable";

type Row = { name: string; n: number };

describe("DataTable", () => {
  it("aligns header and cells the same side", () => {
    render(
      <DataTable<Row>
        columns={[
          { key: "name", label: "名称" },
          { key: "n", label: "数量", align: "right" },
        ]}
        rows={[{ name: "A", n: 1 }]}
        rowKey={(r) => r.name}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "数量" })).toHaveClass("r");
    expect(screen.getByRole("cell", { name: "1" })).toHaveClass("r");
    expect(screen.getByRole("columnheader", { name: "名称" })).not.toHaveClass("r");
  });

  it("shows empty state", () => {
    render(<DataTable<Row> columns={[{ key: "name", label: "名称" }]} rows={[]} rowKey={(r) => r.name} />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });
});
