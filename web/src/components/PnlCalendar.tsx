import type { CalendarDayOut } from "../api/types";
import { num } from "../lib/format";

type Props = { year: number; month: number; days: CalendarDayOut[]; onPrev: () => void; onNext: () => void };

const WEEK = ["一", "二", "三", "四", "五"];

/** 当月每个交易日一格，颜色深浅表示盈亏大小；周末不显示。 */
export function PnlCalendar({ year, month, days, onPrev, onNext }: Props) {
  const byDate = new Map(days.map((d) => [d.date, d]));
  const first = new Date(Date.UTC(year, month - 1, 1));
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const cells: (string | null)[] = [];
  const lead = (first.getUTCDay() + 6) % 7; // 周一为 0
  for (let i = 0; i < Math.min(lead, 5); i++) cells.push(null);
  for (let d = 1; d <= last; d++) {
    const wd = new Date(Date.UTC(year, month - 1, d)).getUTCDay();
    if (wd === 0 || wd === 6) continue;
    cells.push(`${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
  }
  const vals = days.map((d) => d.pnl).filter((v): v is number => v !== null);
  const maxAbs = Math.max(1, ...vals.map(Math.abs));
  const up = vals.filter((v) => v > 0).length;
  const down = vals.filter((v) => v < 0).length;
  return (
    <div className="pnlcal">
      <div className="pnlcal-h">
        <button type="button" className="btn2" aria-label="上个月" onClick={onPrev}>‹</button>
        <b>{year} 年 {month} 月</b>
        <button type="button" className="btn2" aria-label="下个月" onClick={onNext}>›</button>
      </div>
      <div className="pnlcal-g">
        {WEEK.map((w) => <div key={w} className="muted sm wd">{w}</div>)}
        {cells.map((c, i) => {
          const d = c ? byDate.get(c) : undefined;
          const v = d?.pnl ?? null;
          const alpha = v === null ? 0 : 0.15 + 0.75 * (Math.abs(v) / maxAbs);
          const bg = v === null ? undefined : v >= 0 ? `rgba(34,197,94,${alpha})` : `rgba(239,68,68,${alpha})`;
          return (
            <div key={c ?? `pad-${i}`} className="day" style={{ background: bg }} title={v === null ? undefined : num(v, 0)}>
              {c ? Number(c.slice(8)) : ""}
            </div>
          );
        })}
      </div>
      <div className="muted sm">
        上涨 {up} 天 · 下跌 {down} 天{vals.length > 0 && ` · 最好 ${num(Math.max(...vals), 0)} · 最差 ${num(Math.min(...vals), 0)}`}
      </div>
    </div>
  );
}
