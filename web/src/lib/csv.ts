type Cell = string | number | null | undefined;

function field(v: Cell): string {
  const s = v == null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV 文本：开头带 BOM（Excel 按 UTF-8 读中文），含逗号、引号、换行的字段加引号。 */
export function toCsv(rows: Cell[][]): string {
  return "﻿" + rows.map((r) => r.map(field).join(",")).join("\r\n");
}

/** 浏览器下载 CSV；URL 稍后释放（立即释放时部分浏览器下载会失败）。 */
export function downloadCsv(filename: string, rows: Cell[][]): void {
  const url = URL.createObjectURL(new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
