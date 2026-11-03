import type { MarketOverview } from "../api/types";
import { formatDateTimeET } from "../lib/time";

export function MarketFreshness({ market: m }: { market: MarketOverview }) {
  const state = m.quote_delayed ? "行情更新延迟" : m.session === "regular" ? "盘中轮询" : "已收盘 · 最近盘中快照";
  return (
    <div className="sm muted" style={{ marginBottom: 12, lineHeight: 1.8 }} data-testid="market-freshness">
      {m.intraday ? (
        <div>
          {state} · 报价 {formatDateTimeET(m.intraday_time_start ?? m.intraday_time)} 至 {formatDateTimeET(m.intraday_time)} ET
          {m.generated_at && ` · 快照生成 ${formatDateTimeET(m.generated_at)} ET`}
          {m.session === "regular" ? " · 约每 10–15 分钟一轮，非同时报价" : " · 常规时段报价已停止，等待收盘日线"}
        </div>
      ) : <div>价格数据截至 {m.as_of}（美东收盘日线）{m.session === "regular" && " · 盘中快照尚未就绪"}</div>}
      <details>
        <summary>各区块更新范围</summary>
        行情、热力图、板块、风格、价格类宽度与异动、指数当日 K 线跟随价格快照；成交量指标收盘后更新。<br />
        VIX、美债、美元、原油与比特币为日/周频数据，各项显示自己的日期。新闻和财经日历分别显示更新时间。<br />
        账户净值、历史绩效与策略信号来自账户快照及日线；组合页另列持仓盘中估算。Finnhub 报价仅供查看。
      </details>
    </div>
  );
}
