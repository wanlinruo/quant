import type { SysAttemptOut, SysCellOut, SysGridOut } from "../../api/types";
import { ApiState } from "../../components/ApiState";
import { useApi } from "../../hooks/useApi";
import { formatDateTimeET } from "../../lib/time";
import { duration, STATUS_TEXT } from "./labels";

type Pick = { job: string; date: string };
const NO_DETAIL = new Set(["na", "pending", "missing", "blocked"]);

/** 近 30 个交易日 × 任务；点格子看那次运行的每次尝试。 */
export function JobGrid({ grid, onPick }: { grid: SysGridOut; onPick: (p: Pick) => void }) {
  const cellTitle = (job: string, c: SysCellOut) =>
    `${job} · ${c.date} · ${STATUS_TEXT[c.status]}${c.attempts ? ` · ${c.attempts} 次尝试` : ""}${c.duration_s != null ? ` · ${duration(c.duration_s)}` : ""}`;
  return (
    <div className="jobgrid">
      <table data-testid="job-grid">
        <thead>
          <tr>
            <th>任务</th>
            <th className="jg-dates">
              <span>{grid.dates[0]?.slice(5)}</span>
              <span>{grid.dates.at(-1)?.slice(5)}</span>
            </th>
            <th className="r">成功率</th>
            <th className="r">平均耗时</th>
          </tr>
        </thead>
        <tbody>
          {grid.jobs.map((row) => (
            <tr key={row.job}>
              <td className="jg-name">{row.job}</td>
              <td>
                <div className="jg-cells" style={{ gridTemplateColumns: `repeat(${grid.dates.length}, minmax(0, 1fr))` }}>
                  {row.cells.map((c) => (
                    <button
                      key={c.date}
                      type="button"
                      className={`jc ${c.status}`}
                      title={cellTitle(row.job, c)}
                      aria-label={cellTitle(row.job, c)}
                      disabled={NO_DETAIL.has(c.status)}
                      onClick={() => onPick({ job: row.job, date: c.date })}
                    />
                  ))}
                </div>
              </td>
              <td className="r num">{row.success_rate == null ? "—" : `${Math.round(row.success_rate * 100)}%`}</td>
              <td className="r num">{duration(row.avg_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="jg-legend muted">
        {(["success", "retry_success", "failed", "running", "missing", "blocked", "pending", "na"] as const).map((s) => (
          <span key={s}><i className={`jc ${s}`} />{STATUS_TEXT[s]}</span>
        ))}
      </div>
    </div>
  );
}

/** 某次任务的每次尝试（起止、耗时、结果、错误摘要）。 */
export function AttemptsDialog({ mode, pick, onClose }: { mode: string; pick: Pick; onClose: () => void }) {
  const runs = useApi<SysAttemptOut[]>("/api/system/job-runs", { mode, job: pick.job, date: pick.date });
  return (
    <aside className="drawer sys-drawer" role="dialog" aria-label="运行详情">
      <div className="panel-h">
        <b style={{ fontSize: 15 }}>{pick.job} · {pick.date}</b>
        <button type="button" className="btn2" onClick={onClose}>收起</button>
      </div>
      <ApiState result={runs}>
        {(rows) => (
          <div className="attempts">
            {rows.map((a) => (
              <div key={a.attempt} className="attempt">
                <div>
                  <b>第 {a.attempt} 次</b>{" "}
                  <span className={`pill ${a.result === "success" ? "ok" : a.result === "failed" ? "err" : "info"}`}>{STATUS_TEXT[a.result] ?? a.result}</span>{" "}
                  <span className="muted num">{duration(a.duration_s)}</span>
                </div>
                <div className="muted num">{formatDateTimeET(a.started_at)} → {formatDateTimeET(a.finished_at)} ET</div>
                {a.error && <pre className="err-pre">{a.error}</pre>}
              </div>
            ))}
          </div>
        )}
      </ApiState>
    </aside>
  );
}
