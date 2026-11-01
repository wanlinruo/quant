import type { ReactNode } from "react";

type Props = { label: string; value: ReactNode; sub?: ReactNode; live?: boolean; tone?: "up" | "down" };

/** 指标卡；账户为实盘时标题带“实盘”字样（设计 5.6）。 */
export function StatCard({ label, value, sub, live, tone }: Props) {
  return (
    <div className="stat" data-testid="stat-card">
      <div className="l">
        {label}
        {live && <span className="live-tag">实盘</span>}
      </div>
      <div className={`v num${tone ? ` ${tone}` : ""}`}>{value}</div>
      {sub !== undefined && <div className="s num">{sub}</div>}
    </div>
  );
}
