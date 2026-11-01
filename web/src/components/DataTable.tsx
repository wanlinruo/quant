import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";

export type Column<T> = {
  key: string;
  label: string;
  align?: "left" | "right";
  render?: (row: T) => ReactNode;
};

type Props<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  /** 超出时在表格内滚动，表头固定 */
  maxHeight?: number;
};

/** 单元格不换行；表头与数据按同一 align 对齐。 */
export function DataTable<T>({ columns, rows, rowKey, maxHeight }: Props<T>) {
  if (rows.length === 0) return <EmptyState />;
  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="dt">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.align === "right" ? "r" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)}>
              {columns.map((c) => (
                <td key={c.key} className={c.align === "right" ? "r num" : undefined}>
                  {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
