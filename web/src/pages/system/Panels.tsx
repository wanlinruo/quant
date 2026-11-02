import type { SysBannerOut, SysDayOut, SysEventOut, SysQueueOut, SysRiskOut, SysSourceOut, SysTodayOut } from "../../api/types";
import { money, pct } from "../../lib/format";
import { etParts, formatDateTimeET } from "../../lib/time";
import { duration, SOURCE_TEXT, STATUS_TEXT } from "./labels";

const LEVEL_TEXT = { ok: "正常", warn: "需要注意", error: "出错" } as const;
export const PENDING_RESOURCES = "资源采集接入中（#71 B 批）";

export function Banner({ banner }: { banner: SysBannerOut }) {
  const items = banner.items ?? [];
  return (
    <div className={`sys-banner ${banner.level}`} data-testid="banner">
      <b>{LEVEL_TEXT[banner.level]}</b>
      {items.length === 0 ? <span>所有任务与数据正常</span> : (
        <ul>
          {items.map((it, i) => (
            <li key={i} className={it.level}>
              {it.text}
              {it.command && <> · 处理：<code>{it.command}</code></>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Timeline({ rows }: { rows: SysTodayOut[] }) {
  return (
    <ol className="sys-timeline" data-testid="timeline">
      {rows.map((r) => (
        <li key={r.job} className={r.status}>
          <span className="num t">{etParts(r.planned_at).time}</span>
          <div>
            <b>{r.job}</b> <span className="muted">{STATUS_TEXT[r.status] ?? r.status}{r.duration_s != null ? ` · ${duration(r.duration_s)}` : ""}</span>
            {r.error && <div className="err-line">{r.error}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Sources({ rows }: { rows: SysSourceOut[] }) {
  return (
    <table className="tbl sys-tbl">
      <thead><tr><th>数据源</th><th>更新 (ET)</th><th>说明</th><th>状态</th></tr></thead>
      <tbody>
        {rows.map((r) => {
          const [text, cls] = SOURCE_TEXT[r.status] ?? [r.status, "info"];
          return (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td className="num">{formatDateTimeET(r.updated)}</td>
              <td className="muted ellip" title={r.detail}>
                {r.progress != null && <span className="bar"><i style={{ width: `${Math.min(100, r.progress * 100)}%` }} /></span>}
                {r.detail}
              </td>
              <td><span className={`pill ${cls}`}>{text}</span></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function fmt(kind: SysRiskOut["kind"], v: number | null | undefined): string {
  if (v == null) return "—";
  if (kind === "pct") return pct(v, 1);
  if (kind === "usd") return money(v);
  return String(v);
}

export function Risk({ rows }: { rows: SysRiskOut[] }) {
  return (
    <div className="sys-risk">
      {rows.map((r) => {
        const ratio = r.used != null && r.limit ? r.used / r.limit : null;
        return (
          <div key={r.label} className="risk-row">
            <div className="risk-h">
              <span>{r.label}</span>
              <span className="num">{r.kind === "text" ? r.text : r.limit != null ? `${fmt(r.kind, r.used)} / ${fmt(r.kind, r.limit)}` : fmt(r.kind, r.used)}</span>
            </div>
            {ratio != null && <span className={`bar${ratio >= 0.8 ? " hot" : ""}`}><i style={{ width: `${Math.min(100, ratio * 100)}%` }} /></span>}
          </div>
        );
      })}
    </div>
  );
}

const TAG_CLS: Record<string, string> = { 月末调仓: "info", FOMC: "warn", 休市: "err" };

export function Upcoming({ rows }: { rows: SysDayOut[] }) {
  return (
    <table className="tbl sys-tbl">
      <tbody>
        {rows.map((d) => (
          <tr key={d.date}>
            <td className="num">{d.date.slice(5)}</td>
            <td className="muted">{d.weekday}</td>
            <td>{(d.tags ?? []).map((t) => <span key={t} className={`pill ${TAG_CLS[t] ?? (t.startsWith("半日市") ? "warn" : "hold")}`}>{t}</span>)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Events({ rows }: { rows: SysEventOut[] }) {
  if (rows.length === 0) return <div className="muted">近 30 天没有告警</div>;
  return (
    <ul className="sys-events">
      {rows.map((e, i) => (
        <li key={i}>
          <span className={`dot ${e.level}`} />
          <span className="num muted">{formatDateTimeET(e.time).slice(5)}</span>
          <span className="ellip" title={e.text}>{e.text}</span>
        </li>
      ))}
    </ul>
  );
}

export function Queue({ q }: { q: SysQueueOut }) {
  const cells: [string, number | undefined][] = [["排队", q.queued], ["运行中", q.running], ["完成", q.done], ["失败", q.failed]];
  return (
    <div className="sys-queue">
      <div className="q4">
        {cells.map(([l, v]) => <div key={l}><div className="muted">{l}</div><b className="num">{v ?? 0}</b></div>)}
      </div>
      <div className="muted">
        {q.current_id != null ? `运行中：#${q.current_id} ${q.current_strategy ?? ""}，开始于 ${formatDateTimeET(q.current_started)}` : "当前没有运行中的回测"}
      </div>
      <div className="muted">最近完成：{formatDateTimeET(q.last_finished)}</div>
    </div>
  );
}

export function Pending({ text = PENDING_RESOURCES }: { text?: string }) {
  return <div className="muted sys-pending">{text}</div>;
}
