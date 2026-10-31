import type { ReactNode } from "react";

type Props = { label: string; value: ReactNode; sub?: ReactNode; tone?: "up" | "down" };

export function Stat({ label, value, sub, tone }: Props) {
  return (
    <div className="stat">
      <div className="l">{label}</div>
      <div className={`v num${tone ? ` ${tone}` : ""}`}>{value}</div>
      {sub !== undefined && <div className="s num">{sub}</div>}
    </div>
  );
}
