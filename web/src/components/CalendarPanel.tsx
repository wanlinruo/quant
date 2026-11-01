import type { CalItemOut, MarketCalendar } from "../api/types";
import { useApi } from "../hooks/useApi";
import { etParts } from "../lib/time";
import { EmptyState } from "./EmptyState";
import { Panel } from "./Panel";

function when(c: CalItemOut, today: string | null): string {
  const t = etParts(c.time);
  const day = t.date === today ? "今日" : `${t.date.slice(5)} ${t.week}`;
  if (c.kind === "earnings") return `${day} ${c.session ?? "时间未定"}`;
  return c.date_only ? day : `${day} ${t.time}`;
}

function outlook(c: CalItemOut): string {
  const parts = [c.forecast && `预期 ${c.forecast}`, c.previous && `前值 ${c.previous}`].filter(Boolean);
  return parts.length ? parts.join(" · ") : "";
}

/** 首页右栏财经日历：经济数据（Forex Factory + FRED 实际值）、财报（Finnhub）、FOMC。 */
export function CalendarPanel({ height }: { height: number }) {
  const cal = useApi<MarketCalendar>("/api/market/calendar");
  const doc = cal.data?.status === "ok" ? cal.data : null;
  const today = doc?.generated_at ? etParts(doc.generated_at).date : null;
  return (
    <Panel title="财经日历" right={<span className="muted sm">数据 · 财报 · FOMC</span>} height={height}>
      <div className="cal-list" data-testid="calendar" style={{ maxHeight: height - 50 }}>
        {!doc ? (
          <EmptyState kind="pending" />
        ) : (
          <>
            <div className="cal-sub">已公布</div>
            {(doc.released ?? []).length === 0 && <div className="muted sm">—</div>}
            {(doc.released ?? []).map((c) => (
              <div key={c.kind + c.title + c.time} className="cal-row">
                <span className="muted">{when(c, today)}</span>
                <b title={c.title}>{c.title}</b>
                <span className="num">实际 {c.actual ?? "—"}</span>
                <span className="num muted">{outlook(c)}</span>
              </div>
            ))}
            <div className="cal-sub">接下来</div>
            {(doc.upcoming ?? []).length === 0 && <div className="muted sm">—</div>}
            {(doc.upcoming ?? []).map((c) => (
              <div key={c.kind + c.title + c.time} className="cal-row cal-up" data-upcoming>
                <span className="muted">{when(c, today)}</span>
                <b title={c.title}>{c.title}</b>
                <span className="dots" title={`重要性 ${c.impact}/3`}>
                  {[1, 2, 3].map((i) => (i <= c.impact ? <i key={i} data-on /> : <i key={i} />))}
                </span>
                <span className="num muted">{outlook(c)}</span>
              </div>
            ))}
            <div className="muted sm" style={{ marginTop: 6 }}>经济数据每周一更新</div>
          </>
        )}
      </div>
    </Panel>
  );
}
