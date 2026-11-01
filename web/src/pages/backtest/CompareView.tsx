import { useMemo } from "react";
import type { BacktestCompare } from "../../api/types";
import { ApiState } from "../../components/ApiState";
import { EmptyState } from "../../components/EmptyState";
import { Cell, Grid } from "../../components/Grid";
import { Panel } from "../../components/Panel";
import { LineChart, type LineSeriesInput } from "../../charts/LineChart";
import { useApi } from "../../hooks/useApi";
import { num, pct } from "../../lib/format";

const COLORS = ["#2962ff", "#a855f7", "#22c55e", "#f5a623", "#22d3ee"];

function fmt(v: number | null | undefined, kind: string): string {
  if (v == null) return "—";
  return kind === "pct" ? pct(v, 2) : kind === "int" ? String(Math.round(v)) : num(v, 2);
}

function show(v: unknown): string {
  return v === null || v === undefined ? "—" : typeof v === "string" ? v : JSON.stringify(v);
}

function corrStyle(v: number | null | undefined): React.CSSProperties | undefined {
  if (v == null) return undefined;
  return { background: `rgba(41,98,255,${0.1 + 0.6 * Math.max(0, Math.min(1, v))})` };
}

/** 对比模式（设计 5.3）：共同区间净值与回撤叠加、指标并排（最优标绿）、参数差异（不同的标黄）、月收益相关性 */
export function CompareView({ ids }: { ids: number[] }) {
  const res = useApi<BacktestCompare>("/api/backtests/compare", { ids });
  return (
    <div data-testid="compare" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <ApiState result={res}>
        {(d) => {
          if (d.note) return <Panel title="回测对比"><EmptyState note={d.note} /></Panel>;
          const runs = d.runs ?? [];
          const dates = d.dates ?? [];
          const lines = (src: Record<string, (number | null)[]> | undefined): LineSeriesInput[] =>
            runs.map((r, i) => ({
              name: `#${r.id}`,
              color: COLORS[i % COLORS.length],
              points: dates.flatMap((t, k) => {
                const v = src?.[String(r.id)]?.[k];
                return v == null ? [] : [{ time: t, value: v }];
              }),
            }));
          return (
            <>
              <Panel
                title="净值对比（共同区间，起点 = 1）"
                right={
                  <span className="muted sm">
                    共同区间 {d.start} → {d.end}
                    {(d.skipped ?? []).length ? `　已跳过未完成的回测：${(d.skipped ?? []).map((x) => `#${x}`).join("、")}` : ""}
                  </span>
                }
              >
                <LineChart height={280} series={lines(d.series)} logScale />
              </Panel>
              <Panel title="回撤">
                <LineChart height={140} series={lines(d.drawdown)} percent />
              </Panel>
              <Grid>
                <Cell span={6}>
                  <Panel title="指标并排" right={<span className="muted sm">最优标绿</span>}>
                    <table className="dt compact">
                      <thead><tr><th>指标</th>{runs.map((r) => <th key={r.id} className="r">#{r.id}</th>)}</tr></thead>
                      <tbody>
                        {(d.metrics ?? []).map((m) => (
                          <tr key={m.key}>
                            <td>{m.label}{m.scope === "full" && <span className="muted sm">（全区间）</span>}</td>
                            {runs.map((r) => (
                              <td key={r.id} className={`r num${m.best === r.id ? " best" : ""}`}>{fmt(m.values[String(r.id)], m.kind)}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Panel>
                </Cell>
                <Cell span={6}>
                  <Panel title="月收益相关性">
                    <table className="dt compact">
                      <thead><tr><th />{runs.map((r) => <th key={r.id} className="r">#{r.id}</th>)}</tr></thead>
                      <tbody>
                        {(d.corr ?? []).map((row, i) => (
                          <tr key={runs[i]?.id ?? i}>
                            <td>#{runs[i]?.id}</td>
                            {row.map((v, j) => <td key={j} className="r num" style={i === j ? undefined : corrStyle(v)}>{i === j ? "—" : v == null ? "—" : num(v, 2)}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Panel>
                </Cell>
              </Grid>
              <Panel title="参数差异" right={<span className="muted sm">不同的标黄</span>}>
                <ParamDiff d={d} />
              </Panel>
            </>
          );
        }}
      </ApiState>
    </div>
  );
}

function ParamDiff({ d }: { d: BacktestCompare }) {
  const runs = useMemo(() => d.runs ?? [], [d.runs]);
  if (!(d.params ?? []).length) return <EmptyState note="都使用策略默认参数" />;
  return (
    <table className="dt compact">
      <thead><tr><th>参数</th>{runs.map((r) => <th key={r.id} className="r">#{r.id} {r.strategy}</th>)}</tr></thead>
      <tbody>
        {(d.params ?? []).map((p) => (
          <tr key={p.key} className={p.differs ? "changed" : undefined}>
            <td>{p.key}</td>
            {runs.map((r) => <td key={r.id} className="r num">{show(p.values[String(r.id)])}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
