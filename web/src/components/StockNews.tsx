import type { NewsItemOut } from "../api/types";
import { etParts } from "../lib/time";

/** 个股新闻列表（stock_data 任务预取的 Finnhub 公司新闻）：时间（美东）、来源、标题链接。 */
export function StockNews({ items, limit, testId }: { items: NewsItemOut[]; limit?: number; testId: string }) {
  return (
    <div data-testid={testId}>
      {items.slice(0, limit ?? items.length).map((n) => {
        const t = etParts(n.time);
        return (
          <div key={n.url + n.time} className="news-row">
            <span className="muted num" style={{ gridColumn: "1 / 3" }}>{`${t.date.slice(5)} ${t.time}`}</span>
            <span className="muted src">
              {n.category === "earnings" && <span className="tag">财报</span>}
              {n.source}
            </span>
            <a className="hl" href={n.url} target="_blank" rel="noreferrer" title={n.headline}>{n.headline}</a>
          </div>
        );
      })}
    </div>
  );
}
