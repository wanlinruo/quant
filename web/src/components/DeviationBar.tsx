import { signedPct } from "../lib/format";

const SCALE = 0.1; // ±10% 占满半边

/** 偏差 = 实际 − 目标：以中线为 0，向右为超配、向左为低配。 */
export function DeviationBar({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>;
  const w = Math.min(Math.abs(value) / SCALE, 1) * 50;
  return (
    <span className="devbar">
      <span className="track">
        <span className={`fill ${value >= 0 ? "over" : "under"}`} style={value >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
      </span>
      <span className="num">{signedPct(value, 1)}</span>
    </span>
  );
}
