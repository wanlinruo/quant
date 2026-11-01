import type { BacktestAnalysis, BacktestRunOut } from "../../api/types";
import { num, pct } from "../../lib/format";
import { fmtMetric, METRICS } from "../../lib/metrics";
import { STATUS_PILL, STATUS_TEXT } from "../../lib/backtestStatus";

function fmt(v: number | null | undefined, kind: string): string {
  if (v == null) return "—";
  if (kind === "pct") return pct(v, 2);
  if (kind === "int") return String(Math.round(v));
  return num(v, 2);
}

function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : `${Math.round(s)} 秒`;
}

type Props = {
  run: BacktestRunOut;
  a: BacktestAnalysis | null;
  warnings: React.ReactNode;
  onParams: () => void;
  onCopy: () => void;
  onExport: () => void;
};

/** 标题区与统计条（设计 5.3）：统计条每项附 QQQ 对照；分析未就绪时用回测记录里的指标。 */
export function RunHeader({ run, a, warnings, onParams, onCopy, onExport }: Props) {
  return (
    <section className="panel">
      <div className="panel-h">
        <b style={{ fontSize: 16 }}>#{run.id} · {run.strategy}</b>
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {warnings}
          <span className={`pill ${STATUS_PILL[run.status] ?? "hold"}`}>{STATUS_TEXT[run.status] ?? run.status}</span>
          <button type="button" className="btn2" onClick={onParams}>参数</button>
          <button type="button" className="btn2" onClick={onCopy}>复制参数新建</button>
          {/* 单个查看时：对比需要至少两个回测，勾选左侧另一个即可（点这里不会改变当前查看） */}
          <button type="button" className="btn2" disabled title="在左侧再勾选一个回测即可对比">加入对比</button>
          <button type="button" className="btn2" disabled={!a?.series} onClick={onExport}>导出</button>
        </span>
      </div>
      <div className="muted sm">
        {run.start} → {run.end}
        {a?.initial_cash != null && ` · 初始资金 $${a.initial_cash.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
        {a && ` · 用时 ${duration(a.duration_s)}`}
        {run.error ? ` · ${run.error}` : ""}
      </div>
      {a?.bench_note && <div className="sm" style={{ marginTop: 4 }}><span className="pill warn">{a.bench_note}</span></div>}
      <div className="statbar statbar-10" data-testid="statbar">
        {a?.stats?.length
          ? a.stats.map((s) => (
              <div key={s.key} className="stat" title={s.value == null && s.qqq == null ? "按交易口径计算，B 批接入" : undefined}>
                <div className="muted sm">{s.label}</div>
                <div className="num stat-v">{fmt(s.value, s.kind)}</div>
                <div className="muted sm num">{s.qqq != null ? `QQQ ${fmt(s.qqq, s.kind)}` : " "}</div>
              </div>
            ))
          : METRICS.map((m) => (
              <div key={m.key} className="stat">
                <div className="muted sm">{m.label}</div>
                <div className="num stat-v">{fmtMetric(run.metrics[m.key], m.kind)}</div>
              </div>
            ))}
      </div>
    </section>
  );
}
