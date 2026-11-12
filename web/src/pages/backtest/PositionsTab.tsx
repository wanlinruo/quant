import { useState } from "react";
import type { BacktestPositions, BtDailyPosOut, BtTimingOut } from "../../api/types";
import { ApiState } from "../../components/ApiState";
import { EmptyState } from "../../components/EmptyState";
import { Cell, Grid } from "../../components/Grid";
import { useApi } from "../../hooks/useApi";
import { SLEEVE_COLOR, SLEEVE_TEXT } from "../../lib/backtestStatus";
import { pct } from "../../lib/format";

/** 子仓 / 现金占比堆叠面积图（SVG），择时减仓期用浅色底标出 */
function Stack({ daily, timing }: { daily: BtDailyPosOut[]; timing: BtTimingOut[] }) {
  const d = { daily, timing };
  const W = 800;
  const H = 220;
  const n = d.daily.length;
  if (n < 2) return <EmptyState />;
  const names = [...new Set(d.daily.flatMap((r) => Object.keys(r.sleeves)))].sort();
  const x = (i: number) => (i / (n - 1)) * W;
  const idx = new Map(d.daily.map((r, i) => [r.date, i]));
  const layers: { key: string; color: string; pts: string }[] = [];
  const base = new Array<number>(n).fill(0);
  for (const key of [...names, "现金"]) {
    const top = d.daily.map((r, i) => base[i] + (key === "现金" ? r.cash : r.sleeves[key] ?? 0));
    const upper = top.map((v, i) => `${x(i)},${H - Math.min(v, 1) * H}`);
    const lower = base.map((v, i) => `${x(i)},${H - Math.min(v, 1) * H}`).reverse();
    layers.push({ key, color: key === "现金" ? "#2a2f3b" : SLEEVE_COLOR[key] ?? "#787b86", pts: [...upper, ...lower].join(" ") });
    top.forEach((v, i) => (base[i] = v));
  }
  return (
    <>
      <svg data-testid="stack" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} role="img" aria-label="子仓与现金占比">
        {d.timing.map((t) => {
          const a = idx.get(t.start) ?? 0;
          const b = idx.get(t.end) ?? a;
          return <rect key={t.start} className="timing" x={x(a)} y={0} width={Math.max(x(b) - x(a), 1)} height={H} fill="rgba(245,166,35,0.15)" />;
        })}
        {layers.map((l) => <polygon key={l.key} points={l.pts} fill={l.color} opacity={0.85} />)}
      </svg>
      <div className="muted sm" style={{ display: "flex", gap: 12, marginTop: 4 }}>
        {layers.map((l) => <span key={l.key}><i className="dot" style={{ background: l.color }} />{l.key === "现金" ? "现金" : SLEEVE_TEXT[l.key] ?? l.key}</span>)}
        <span><i className="dot" style={{ background: "rgba(245,166,35,0.5)" }} />择时减仓期</span>
        <span>{d.daily[0].date} → {d.daily[n - 1].date}</span>
      </div>
    </>
  );
}

/** 持仓变化：子仓 / 现金占比随时间变化、选一天看当天持仓、持有时间最长的交易 */
export function PositionsTab({ runId }: { runId: number }) {
  const [day, setDay] = useState<string | null>(null);
  const res = useApi<BacktestPositions>(`/api/backtests/${runId}/positions`);
  const selected = useApi<BacktestPositions>(day ? `/api/backtests/${runId}/positions` : null, day ? { date: day } : undefined);
  const holdingsTable = (d: BacktestPositions) => (
    <table className="dt compact">
      <thead><tr><th>代码</th><th>子仓</th><th className="r">权重</th></tr></thead>
      <tbody>{(d.holdings ?? []).map((h) => <tr key={`${h.symbol}-${h.sleeve}`}><td>{h.symbol}</td><td className="muted">{SLEEVE_TEXT[h.sleeve] ?? h.sleeve}</td><td className="r num">{pct(h.weight, 1)}</td></tr>)}</tbody>
    </table>
  );
  return (
    <div data-testid="tab-持仓变化">
      <ApiState result={res}>
        {(d) => {
          if (d.note) return <EmptyState note={d.note} />;
          const first = d.daily?.[0]?.date;
          const last = d.daily?.at(-1)?.date;
          return (
            <>
              <div className="subpanel"><div className="subpanel-h"><b>子仓与现金占比</b></div><Stack daily={d.daily ?? []} timing={d.timing ?? []} /></div>
              <Grid>
                <Cell span={6}>
                  <div className="subpanel">
                    <div className="subpanel-h">
                      <b>当天持仓</b>
                      <input type="date" aria-label="选择日期" value={day ?? d.day ?? ""} min={first} max={last} onChange={(e) => setDay(e.target.value || null)} />
                    </div>
                    {day ? <ApiState result={selected}>{holdingsTable}</ApiState> : holdingsTable(d)}
                  </div>
                </Cell>
                <Cell span={6}>
                  <div className="subpanel">
                    <div className="subpanel-h"><b>持有时间最长</b><span className="muted sm">单笔交易</span></div>
                    <table className="dt compact">
                      <thead><tr><th>代码</th><th>子仓</th><th>开仓</th><th>平仓</th><th className="r">天数</th></tr></thead>
                      <tbody>{(d.longest ?? []).map((t, i) => <tr key={`${t.symbol}-${t.sleeve}-${t.open_date}-${t.close_date ?? "open"}-${i}`}><td>{t.symbol}</td><td className="muted">{SLEEVE_TEXT[t.sleeve] ?? t.sleeve}</td><td>{t.open_date}</td><td>{t.close_date ?? "持有中"}</td><td className="r num">{t.days} 天</td></tr>)}</tbody>
                    </table>
                  </div>
                </Cell>
              </Grid>
            </>
          );
        }}
      </ApiState>
    </div>
  );
}
