import type { SysContainerOut, SysDiskOut, SysResourceOut, SysTrendOut } from "../../api/types";
import { Sparkline } from "../../components/Sparkline";
import { Stat } from "../../components/Stat";
import { formatDateTimeET } from "../../lib/time";

export const NO_SAMPLES = "暂无资源采样（monitor 未运行）";
const GB = 1024 ** 3;

export function ResourceCard({ c }: { c: SysResourceOut }) {
  const pts = c.series ?? [];
  const sampleCount = pts.filter((v) => v != null).length;
  return (
    <div className={`sys-card ${c.level ?? "info"}`}>
      <Stat
        label={c.label}
        value={c.value == null ? "—" : `${c.value.toFixed(1)}%`}
        sub={
          <span className="res-sub">
            <span>{c.text || (c.key === "latency" ? "" : "24 小时")}</span>
            {sampleCount > 1 && <Sparkline values={pts} width={110} height={20} />}
          </span>
        }
      />
    </div>
  );
}

function uptime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!(s > 0)) return "—";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时`;
  return `${Math.floor(s / 86400)} 天`;
}

const STATE_CLS: Record<string, string> = { running: "ok", restarting: "warn", exited: "err", dead: "err" };

export function Containers({ rows, version }: { rows: SysContainerOut[]; version?: string | null }) {
  if (rows.length === 0) return <div className="muted">{NO_SAMPLES}</div>;
  return (
    <>
      <table className="tbl sys-tbl">
        <thead><tr><th>容器</th><th>状态</th><th className="r" title="与 docker stats 一致：单核 100%，多核可超过 100%">CPU（单核 %）</th><th className="r">内存</th><th>已运行</th><th className="r">重启</th></tr></thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td><span className={`pill ${c.stale ? "hold" : STATE_CLS[c.state ?? ""] ?? "info"}`} title={c.status ?? ""}>{c.stale ? "失联" : c.state ?? "—"}</span></td>
              <td className="r num">{c.cpu_pct == null ? "—" : `${c.cpu_pct.toFixed(1)}%`}</td>
              <td className="r num">{c.mem_bytes == null ? "—" : `${(c.mem_bytes / GB).toFixed(2)} GB`}{c.mem_limit ? <span className="muted"> / {(c.mem_limit / GB).toFixed(1)}</span> : null}</td>
              <td className="num">{c.state === "running" ? uptime(c.started_at) : "—"}</td>
              <td className="r num">{c.restarts ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted" style={{ marginTop: 8 }}>当前版本：<code>{version ?? "未知"}</code></div>
    </>
  );
}

const TREND = [
  { key: "cpu", label: "CPU", color: "#60a5fa" },
  { key: "mem", label: "内存", color: "#a78bfa" },
  { key: "disk", label: "磁盘", color: "#f59e0b" },
] as const;

/** 7 天每小时均值，纵轴 0–100%。 */
export function TrendChart({ t, height = 110 }: { t: SysTrendOut; height?: number }) {
  const times = t.times ?? [];
  if (times.length < 2 || !(t.cpu ?? []).some((v) => v != null)) return <div className="muted">{NO_SAMPLES}</div>;
  const W = 1000;
  const x = (i: number) => (i / (times.length - 1)) * W;
  const y = (v: number) => height - (Math.min(100, Math.max(0, v)) / 100) * height;
  /** 连续有值的点分成一段，缺数据的小时断开；只有一个点的段不画。 */
  const segments = (vals: (number | null)[]) => {
    const out: string[][] = [[]];
    vals.forEach((v, i) => (v == null ? out.push([]) : out[out.length - 1].push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`)));
    return out.filter((seg) => seg.length > 1).map((seg) => seg.join(" "));
  };
  return (
    <div data-testid="trend">
      <div className="trend-legend">
        {TREND.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
        <span className="muted">{formatDateTimeET(times[0])} → {formatDateTimeET(times.at(-1))} ET</span>
      </div>
      <svg width="100%" height={height} viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none">
        {[25, 50, 75].map((g) => <line key={g} x1={0} x2={W} y1={y(g)} y2={y(g)} stroke="#1d212b" />)}
        {TREND.flatMap((s) =>
          segments(t[s.key] ?? []).map((pts, i) => (
            <polyline key={`${s.key}-${i}`} className={s.key} points={pts} fill="none" stroke={s.color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          )),
        )}
      </svg>
    </div>
  );
}

export function DiskForecast({ d }: { d: SysDiskOut }) {
  if (d.used == null || !d.total) return <div className="muted">{NO_SAMPLES}</div>;
  const pct = (d.used / d.total) * 100;
  return (
    <div className="sys-disk">
      <div className="risk-h"><span>已用</span><span className="num">{(d.used / GB).toFixed(1)} / {(d.total / GB).toFixed(1)} GB（{pct.toFixed(0)}%）</span></div>
      <span className={`bar${pct >= 85 ? " hot" : ""}`}><i style={{ width: `${Math.min(100, pct)}%` }} /></span>
      <b className={d.days_left != null && d.days_left < 30 ? "down" : ""}>
        {d.days_left == null ? "暂无写满风险" : `约 ${Math.round(d.days_left)} 天后写满`}
      </b>
      <div className="muted">{d.note}</div>
    </div>
  );
}
