import { useEffect, useId, useRef, useState } from "react";
import type { MarketOverview } from "../api/types";
import { etParts, formatDateTimeET } from "../lib/time";

function quoteRange(start: string | null | undefined, end: string | null | undefined): string {
  if (!end) return "—";
  const a = etParts(start ?? end);
  const b = etParts(end);
  return `${a.date} ${a.time}${a.date === b.date ? (a.time === b.time ? "" : `–${b.time}`) : ` 至 ${b.date} ${b.time}`} ET`;
}

export function MarketFreshness({ market: m, lastJobFinished }: { market: MarketOverview; lastJobFinished?: string | null }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const id = useId();
  const regular = m.session === "regular";
  const delayed = m.quote_delayed || m.stale;
  const state = delayed ? "更新延迟" : m.intraday ? (regular ? "盘中" : "已收盘") : regular ? "采集中" : "收盘日线";
  const tone = delayed ? "delayed" : m.intraday && regular ? "live" : regular ? "collecting" : "closed";
  const latest = m.intraday && m.intraday_time ? etParts(m.intraday_time) : null;
  const time = latest ? `${latest.date.slice(5)} ${latest.time} ET` : `${m.as_of?.slice(5) ?? "—"} 收盘`;

  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const outside = (event: Event) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", keydown);
    };
  }, [open]);

  return (
    <div className="market-freshness" data-testid="market-freshness" ref={root}>
      <span className={`market-status is-${tone}`} role="status">{state}</span>
      <span className="market-quote-time num" title={m.intraday ? `行情最新报价 ${formatDateTimeET(m.intraday_time)} ET` : `价格数据截至 ${m.as_of}（美东收盘日线）`}>
        行情 {time}
      </span>
      <button className="market-info-button" ref={trigger} type="button" aria-label="查看数据说明" aria-expanded={open} aria-controls={id} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <circle cx="10" cy="10" r="7.25" /><path d="M10 9v5M10 6v1" />
        </svg>
        <span>数据说明</span>
      </button>
      {open && (
        <section className="market-info-popover" id={id} role="dialog" aria-labelledby={`${id}-title`}>
          <header className="market-info-header">
            <div><h2 id={`${id}-title`}>数据与更新</h2><span>各区块按各自数据源更新 · 美东时间</span></div>
            <button type="button" className="market-info-close" ref={closeButton} aria-label="关闭数据说明" onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button>
          </header>
          <dl className="market-info-times">
            <div><dt>{m.intraday ? "报价区间" : "行情日期"}</dt><dd>{m.intraday ? quoteRange(m.intraday_time_start, m.intraday_time) : `${m.as_of ?? "—"} 收盘`}</dd></div>
            {m.generated_at && <div><dt>快照生成</dt><dd>{formatDateTimeET(m.generated_at)} ET</dd></div>}
            {lastJobFinished && <div title="最近成功后台任务的完成时间，不代表行情时间"><dt>最近任务</dt><dd>{formatDateTimeET(lastJobFinished)} ET</dd></div>}
          </dl>
          {(delayed || (m.intraday && !regular) || (!m.intraday && regular)) && (
            <p className={`market-info-notice${delayed ? " is-delayed" : ""}`}>
              {delayed ? "行情更新延迟，当前显示最近可用数据。" : m.intraday ? "常规时段报价已停止，等待收盘日线。" : "盘中快照尚未就绪，暂用最近收盘日线。"}
            </p>
          )}
          <div className="market-info-scope">
            <div><b>行情与图表</b><p>盘中约每 10–15 分钟一轮，非同时报价。成交量指标收盘后更新。</p></div>
            <div><b>宏观与资讯</b><p>宏观为日／周频；新闻、财经日历的更新时间见各自区块。</p></div>
            <div><b>账户与策略</b><p>按账户快照与日线显示；持仓盘中估算见组合页。</p></div>
          </div>
          <footer>Finnhub 盘中报价仅供查看</footer>
        </section>
      )}
    </div>
  );
}
