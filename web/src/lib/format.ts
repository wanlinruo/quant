export function pct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `${(v * 100).toFixed(digits)}%`;
}

export function signedPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  const s = (v * 100).toFixed(digits);
  return `${v > 0 ? "+" : ""}${s}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "-";
  return `$${num(v, 2)}`;
}
