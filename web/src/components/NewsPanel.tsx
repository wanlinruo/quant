import { useState } from "react";
import type { MarketNews } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useETToday } from "../hooks/useETToday";
import { etParts } from "../lib/time";
import { EmptyState } from "./EmptyState";
import { Panel } from "./Panel";
import { Tabs } from "./Tabs";

const FILTERS = [
  { key: "all", label: "全部" },
  { key: "macro", label: "宏观" },
  { key: "mine", label: "持仓" },
  { key: "earnings", label: "财报" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

/** 首页右栏新闻（Finnhub，market_extras 任务生成）；持仓筛选按当前账户持仓。 */
export function NewsPanel({ height, mine }: { height: number; mine: Set<string> }) {
  const news = useApi<MarketNews>("/api/market/news", undefined, 3 * 60_000);
  const [f, setF] = useState<FilterKey>("all");
  const doc = news.data?.status === "ok" ? news.data : null;
  const today = useETToday();
  const items = (doc?.items ?? []).filter((n) =>
    f === "macro" ? n.category === "macro" : f === "earnings" ? n.category === "earnings" : f === "mine" ? n.tags.some((t) => mine.has(t)) : true,
  );
  return (
    <Panel title="新闻" right={<Tabs tabs={FILTERS.map((x) => ({ key: x.key, label: x.label }))} value={f} onChange={setF} />} height={height}>
      <div className="news-list" data-testid="news" style={{ maxHeight: height - 50 }}>
        {doc?.generated_at && (
          <div className="muted sm news-upd">
            资讯更新于 {etParts(doc.generated_at).date} {etParts(doc.generated_at).time} ET · 交易时段约 15–25 分钟刷新
          </div>
        )}
        {!doc ? (
          <EmptyState kind="pending" />
        ) : items.length === 0 ? (
          <EmptyState />
        ) : (
          items.map((n) => {
            const t = etParts(n.time);
            return (
              <div key={n.url + n.time} className="news-row">
                <span className="muted num">{t.date === today ? t.time : t.date.slice(5)}</span>
                <span>
                  {n.tags.length ? n.tags.map((s) => <span key={s} className="tag">{s}</span>) : <span className="tag">宏观</span>}
                </span>
                <span className="muted src">{n.source}</span>
                <a className="hl" href={n.url} target="_blank" rel="noreferrer" title={n.headline}>{n.headline}</a>
              </div>
            );
          })
        )}
      </div>
    </Panel>
  );
}
