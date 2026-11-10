import { useEffect } from "react";
import type { RbCellOut, RbMetricsOut, RbRowOut, RobustnessOut } from "../../api/types";
import { ApiState } from "../../components/ApiState";
import { EmptyState } from "../../components/EmptyState";
import { useApi } from "../../hooks/useApi";
import { STATUS_PILL, STATUS_TEXT } from "../../lib/backtestStatus";
import { num, pct, signedPct } from "../../lib/format";
import { Sub } from "./AnalysisTabs";
import { ROBUST_REFRESH_MS } from "./robustRefresh";

/** 夏普着色：≤ 0.5 红，1.0 中性，≥ 1.5 绿。 */
function heat(sharpe: number | null | undefined): string {
  if (sharpe == null) return "transparent";
  const t = Math.max(-1, Math.min(1, (sharpe - 1) / 0.5));
  return t >= 0 ? `rgba(34,197,94,${0.12 + 0.45 * t})` : `rgba(239,68,68,${0.12 - 0.45 * t})`;
}

function Cell({ c }: { c: RbCellOut }) {
  const m = c.metrics;
  const body = (
    <>
      {m ? (
        <>
          <b className="num">{num(m.sharpe, 2)}</b>
          <span className="num sm">{pct(m.cagr, 1)} · {pct(m.max_drawdown, 0)}</span>
        </>
      ) : c.status === "skipped" ? <span className="muted">—</span> : <span className={`pill ${STATUS_PILL[c.status] ?? "hold"}`} title={c.error ?? undefined}>{STATUS_TEXT[c.status] ?? c.status}</span>}
    </>
  );
  const props = { className: `rb-cell ${c.status}${c.center ? " center" : ""}`, style: { background: heat(m?.sharpe) }, title: c.label };
  return c.run_id ? <a {...props} href={`/backtest/${c.run_id}`} aria-label={`${c.label}，打开回测 #${c.run_id}`}>{body}</a> : <div {...props}>{body}</div>;
}

function Mcols({ m }: { m: RbMetricsOut | null | undefined }) {
  return (
    <>
      <td className="r num">{pct(m?.cagr, 1)}</td>
      <td className="r num">{num(m?.sharpe, 2)}</td>
      <td className="r num">{pct(m?.max_drawdown, 1)}</td>
    </>
  );
}

function Rows({ rows, verdict }: { rows: RbRowOut[]; verdict?: boolean }) {
  return (
    <table className="dt compact">
      <thead><tr><th>设置</th><th className="r">年化</th><th className="r">夏普</th><th className="r">最大回撤</th><th>状态</th>{verdict && <th>判定</th>}</tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.label}-${r.run_id}`} className={r.base ? "base-row" : ""}>
            <td>{r.run_id ? <a href={`/backtest/${r.run_id}`}>{r.label}</a> : r.label}</td>
            <Mcols m={r.metrics} />
            <td><span className={`pill ${STATUS_PILL[r.status] ?? "hold"}`} title={r.error ?? undefined}>{STATUS_TEXT[r.status] ?? r.status}</span></td>
            {verdict && <td>{r.passed === true ? <span className="pill ok">通过</span> : r.passed === false ? <span className="pill err">未通过</span> : "—"}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 稳健性：参数热力图、相邻参数、walk-forward、成本与起始时间敏感性（#68）。 */
export function RobustTab({ runId, strategy }: { runId: number; strategy: string }) {
  const res = useApi<RobustnessOut>(`/api/backtests/${runId}/robustness`);
  const pending = res.data?.status === "pending";
  const { reload } = res;
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(reload, ROBUST_REFRESH_MS);
    return () => clearInterval(t);
  }, [pending, reload]);
  return (
    <ApiState result={res}>
      {(d) => {
        if (d.status === "not_requested" || (!d.grid && !d.costs?.length)) return <EmptyState kind="pending" note={d.note || "未运行"} />;
        const p = d.progress;
        const grid = d.grid;
        const wf = d.walk_forward;
        return (
          <div className="rb" data-testid="tab-稳健性">
            {p && (
              <div className={`rb-progress ${d.status}`}>
                子回测已完成 {p.done} / {p.total}
                {p.failed > 0 && <span className="pill err">失败 {p.failed}</span>}
                {d.status === "pending" && <span className="muted">（排队 {p.queued}、运行中 {p.running}；worker 串行执行，每个约 3 分钟）</span>}
              </div>
            )}
            <div className="rb-grid2">
              {grid && (
                <Sub title="参数敏感性" note="格内：夏普 / 年化 · 最大回撤；中间为本次">
                  <div className="rb-heat" data-testid="rb-grid">
                    <span />
                    {grid.x_values.map((v) => <span key={v} className="muted sm ax">{v}</span>)}
                    {[0, 1, 2].map((y) => (
                      <div key={y} className="rb-row">
                        <span className="muted sm ax">{grid.y_values[y]}</span>
                        {[0, 1, 2].map((x) => {
                          const c = grid.cells.find((k) => k.x === x && k.y === y);
                          return c ? <Cell key={x} c={c} /> : <span key={x} />;
                        })}
                      </div>
                    ))}
                  </div>
                  <div className="muted sm">横轴：{grid.x_label}；纵轴：{grid.y_label}</div>
                </Sub>
              )}
              <Sub title="相邻参数逐个检查" note={d.neighbors_note}>
                {strategy === "combo" && d.neighbors_verdict != null && (
                  <div className={`pill ${d.neighbors_verdict ? "ok" : "err"}`} style={{ marginBottom: 6 }}>{d.neighbors_verdict ? "全部通过" : "未全部通过"}</div>
                )}
                <Rows rows={d.neighbors ?? []} verdict={strategy === "combo"} />
              </Sub>
            </div>
            <Sub title={`滚动样本外检验（walk-forward，训练 ${wf?.train_years ?? 3} 年 / 测试 1 年）`} note={wf?.note}>
              {!wf ? <div className="muted">参数网格的子回测完成后计算</div> : wf.segments?.length ? (
                <div className="rb-grid2">
                  <table className="dt compact">
                    <thead><tr><th>测试年</th><th>选中的参数</th><th className="r">样本外收益</th><th className="r">本次参数</th></tr></thead>
                    <tbody>
                      {wf.segments.map((s) => (
                        <tr key={s.year}><td className="num">{s.year}</td><td>{s.chosen}</td><td className="r num">{signedPct(s.test_return, 1)}</td><td className="r num">{signedPct(s.base_return, 1)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <table className="dt compact">
                    <thead><tr><th>同期</th><th className="r">年化</th><th className="r">夏普</th><th className="r">最大回撤</th></tr></thead>
                    <tbody>
                      <tr><td>滚动选参（样本外）</td><Mcols m={wf.strategy} /></tr>
                      <tr><td>本次参数</td><Mcols m={wf.base} /></tr>
                    </tbody>
                  </table>
                </div>
              ) : <div className="muted">{wf.note}</div>}
            </Sub>
            <div className="rb-grid2">
              <Sub title="成本敏感性" note="佣金、平台费、滑点同比例放大"><Rows rows={d.costs ?? []} /></Sub>
              <Sub title="起始时间敏感性" note="起点推后，终点不变"><Rows rows={d.starts ?? []} /></Sub>
            </div>
          </div>
        );
      }}
    </ApiState>
  );
}
