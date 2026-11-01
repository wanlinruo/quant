import { num } from "../lib/format";

/** 52 周区间条：当前价在最低与最高之间的位置。 */
export function RangeBar({ low, high, value }: { low: number; high: number; value: number }) {
  const pos = high > low ? Math.min(1, Math.max(0, (value - low) / (high - low))) : 0.5;
  return (
    <div>
      <div className="rangebar" aria-label="52 周区间">
        <span className="pin" style={{ left: `calc(${pos * 100}% - 1px)` }} />
      </div>
      <div className="muted sm num" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>{num(low)}</span>
        <span>{num(high)}</span>
      </div>
    </div>
  );
}
