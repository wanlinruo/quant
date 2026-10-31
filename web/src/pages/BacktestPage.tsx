import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import type { BacktestDefaults, BacktestRunOut, EquityPoint } from "../api/types";
import { ApiState } from "../components/ApiState";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { Stat } from "../components/Stat";
import { LineChart } from "../charts/LineChart";
import { useApi } from "../hooks/useApi";
import { pct } from "../lib/format";
import { fmtMetric, METRICS } from "../lib/metrics";
import { BacktestSubmitDrawer } from "./BacktestSubmitDrawer";

const MAX_COMPARE = 5;
const COLORS = ["#2962ff", "#a855f7", "#22c55e", "#f5a623", "#22d3ee"];
const STATUS_PILL: Record<string, string> = { done: "ok", running: "info", queued: "hold", failed: "err" };
const STATUS_TEXT: Record<string, string> = { done: "完成", running: "运行中", queued: "排队", failed: "失败" };

function metric(run: BacktestRunOut, key: string): number | undefined {
  const v = run.metrics[key];
  return typeof v === "number" ? v : undefined;
}

export function BacktestPage() {
  const runs = useApi<BacktestRunOut[]>("/api/backtests");
  const defaults = useApi<BacktestDefaults>("/api/backtests/defaults");
  const [checked, setChecked] = useState<number[] | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const { id: routeId } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();

  const list = runs.data ?? [];
  const firstDone = list.find((r) => r.status === "done") ?? list[0];
  const defaultId = list.find((r) => String(r.id) === routeId)?.id ?? firstDone?.id;
  const selected = useMemo(() => checked ?? (defaultId !== undefined ? [defaultId] : []), [checked, defaultId]);
  const current = list.find((r) => r.id === selected[0]);
  const equity = useApi<EquityPoint[]>(selected.length ? "/api/backtests/equity" : null, { ids: selected });

  const series = useMemo(() => {
    const pts = equity.data ?? [];
    return selected.map((id, i) => {
      const own = pts.filter((p) => p.run_id === id);
      const base = own[0]?.equity || 1;
      return { name: `#${id}`, color: COLORS[i % COLORS.length], points: own.map((p) => ({ time: p.date, value: p.equity / base })) };
    });
  }, [equity.data, selected]);
  const ddSeries = useMemo(
    () => selected.map((id, i) => ({
      name: `#${id}`, color: COLORS[i % COLORS.length],
      points: (equity.data ?? []).filter((p) => p.run_id === id).map((p) => ({ time: p.date, value: p.drawdown })),
    })),
    [equity.data, selected],
  );

  /** 点行：设为当前查看（排在第一位），对比集合满 5 个时挤掉最后一个；同步网址。 */
  function open(id: number) {
    setChecked([id, ...selected.filter((x) => x !== id)].slice(0, MAX_COMPARE));
    navigate({ pathname: `/backtest/${id}`, search });
  }

  function toggle(id: number) {
    setChecked(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  return (
    <div className="page">
      {notice && <div className="pill ok" style={{ alignSelf: "flex-start", padding: "4px 10px" }}>{notice}</div>}
      <Grid>
        <Cell span={3}>
          <Panel
            title="回测记录"
            right={<button type="button" className="btn" disabled={!defaults.data} onClick={() => setDrawer(true)}>+ 新建回测</button>}
          >
            <ApiState result={runs}>
              {(rs) => (
                <div className="runs">
                  {rs.map((r) => {
                    const on = selected.includes(r.id);
                    return (
                      <div key={r.id} className={`run${r.id === current?.id ? " on" : ""}`} onClick={() => open(r.id)}>
                        <input
                          type="checkbox"
                          aria-label={`对比 #${r.id}`}
                          checked={on}
                          disabled={!on && selected.length >= MAX_COMPARE}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => toggle(r.id)}
                        />
                        <span className={`pill ${STATUS_PILL[r.status] ?? "hold"}`}>{STATUS_TEXT[r.status] ?? r.status}</span>
                        <b>#{r.id}</b>
                        <span className="muted">{r.strategy}</span>
                        <span style={{ flex: 1 }} />
                        <span className="num up">{metric(r, "cagr") !== undefined ? pct(metric(r, "cagr"), 1) : ""}</span>
                        <span className="num down">{metric(r, "max_drawdown") !== undefined ? pct(metric(r, "max_drawdown"), 1) : ""}</span>
                      </div>
                    );
                  })}
                  <div className="muted sm" style={{ marginTop: 6 }}>勾选最多 {MAX_COMPARE} 个进入对比</div>
                </div>
              )}
            </ApiState>
          </Panel>
        </Cell>
        <Cell span={9}>
          {current ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <section className="panel">
                <div className="panel-h">
                  <b style={{ fontSize: 16 }}>#{current.id} · {current.strategy}</b>
                  <span className={`pill ${STATUS_PILL[current.status] ?? "hold"}`}>{STATUS_TEXT[current.status] ?? current.status}</span>
                </div>
                <div className="muted sm">{current.start} → {current.end}{current.error ? ` · ${current.error}` : ""}</div>
                <div className="statbar">
                  {METRICS.map((m) => (
                    <Stat key={m.key} label={m.label} value={fmtMetric(current.metrics[m.key], m.kind)} />
                  ))}
                </div>
              </section>
              <Panel title="净值（起点 = 1）">
                <ApiState result={equity}>{() => <LineChart height={300} series={series} />}</ApiState>
              </Panel>
              <Panel title="回撤">
                <ApiState result={equity}>{() => <LineChart height={160} series={ddSeries} percent />}</ApiState>
              </Panel>
              <Panel title="指标对比">
                <DataTable<BacktestRunOut>
                  rowKey={(r) => r.id}
                  rows={list.filter((r) => selected.includes(r.id))}
                  columns={[
                    { key: "id", label: "回测", render: (r) => `#${r.id} ${r.strategy} ${r.start}~${r.end}` },
                    ...METRICS.map((m) => ({ key: m.key, label: m.label, align: "right" as const, render: (r: BacktestRunOut) => fmtMetric(r.metrics[m.key], m.kind) })),
                  ]}
                />
              </Panel>
            </div>
          ) : (
            <Panel title="回测报告"><EmptyState note="还没有回测记录，点击“+ 新建回测”提交" /></Panel>
          )}
        </Cell>
      </Grid>
      {drawer && defaults.data && (
        <BacktestSubmitDrawer
          defaults={defaults.data}
          onClose={() => setDrawer(false)}
          onSubmitted={(id) => {
            setDrawer(false);
            setNotice(`已提交回测 #${id}，backtest-worker 执行完成后显示在列表中`);
            runs.reload();
          }}
        />
      )}
    </div>
  );
}
