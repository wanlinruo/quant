import { useMemo, useState } from "react";
import type { BtSeriesOut } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { Panel } from "../../components/Panel";
import { LineChart, type LineSeriesInput } from "../../charts/LineChart";

function line(name: string, color: string, dates: string[], vals: (number | null)[] | null | undefined, dash = false): LineSeriesInput | null {
  if (!vals) return null;
  return { name, color, dash, points: dates.flatMap((d, i) => (vals[i] == null ? [] : [{ time: d, value: vals[i] as number }])) };
}

/** 主图（常驻，切换标签时不重新挂载）：策略、QQQ、SPY 净值（起点 = 1，对数 / 线性），样本外蓝底；回撤对比。 */
export function MainChart({ s, strategy, oosStart, marks }: { s: BtSeriesOut | null | undefined; strategy: string; oosStart: string; marks?: string[] }) {
  const [log, setLog] = useState(true);
  const nav = useMemo(() => {
    if (!s) return [];
    const d = s.dates;
    return [line(strategy, "#2962ff", d, s.strategy), line("QQQ", "#f5a623", d, s.qqq, true), line("SPY", "#787b86", d, s.spy, true)].filter((x): x is LineSeriesInput => x !== null);
  }, [s, strategy]);
  const dd = useMemo(() => {
    if (!s) return [];
    return [line(strategy, "#ef4444", s.dates, s.drawdown), line("QQQ", "#f5a623", s.dates, s.qqq_drawdown, true)].filter((x): x is LineSeriesInput => x !== null);
  }, [s, strategy]);
  return (
    <div data-testid="main-chart" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Panel
        title="净值（起点 = 1）"
        right={
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="muted sm">蓝底为样本外（{oosStart} 起）</span>
            <button type="button" className="btn2" aria-pressed={log} onClick={() => setLog(true)}>对数</button>
            <button type="button" className="btn2" aria-pressed={!log} onClick={() => setLog(false)}>线性</button>
          </span>
        }
      >
        {nav.length ? <LineChart height={300} series={nav} logScale={log} shadeFrom={oosStart} markers={marks} markerText="开 / 平" /> : <EmptyState />}
      </Panel>
      <Panel title="回撤对比">{dd.length ? <LineChart height={140} series={dd} percent /> : <EmptyState />}</Panel>
    </div>
  );
}
