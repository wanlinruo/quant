import { useMemo, useState } from "react";
import { Link } from "react-router";
import type {
  CalendarDayOut,
  ClosedTradeOut,
  ClosedTradesOut,
  ContributionRow,
  FillOut,
  HoldingRow,
  OrderOut,
  PerformanceOut,
  PortfolioOverview,
  RebalancePreview,
} from "../api/types";
import { ApiState } from "../components/ApiState";
import { DataTable } from "../components/DataTable";
import { DeviationBar } from "../components/DeviationBar";
import { Donut } from "../components/Donut";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { PnlCalendar } from "../components/PnlCalendar";
import { StatCard } from "../components/StatCard";
import { Tabs } from "../components/Tabs";
import { LineChart } from "../charts/LineChart";
import { useApi } from "../hooks/useApi";
import { money, num, pct, signedMoney, signedPct } from "../lib/format";
import { formatDateTimeET } from "../lib/time";
import { useShell } from "../shell";

const NA = "数据不足";
const ROW2_H = 400;
const ROW4_H = 330;
const ROW5_H = 380;
const SLEEVE_TEXT: Record<string, string> = { momentum: "动量仓", swing: "波段仓", both: "波段+动量", exit: "待卖出", cash: "现金", other: "其他" };
const SLEEVE_COLOR: Record<string, string> = { momentum: "#a855f7", swing: "#2962ff", cash: "#4b5263", other: "#f5a623" };
const STATE_CLASS: Record<string, string> = { ok: "ok", near: "warn", exit: "err" };
const RANGES = [
  { key: "1w", label: "1周", days: 7 },
  { key: "1m", label: "1月", days: 31 },
  { key: "3m", label: "3月", days: 92 },
  { key: "all", label: "全部", days: 0 },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

function orNA(v: number | null | undefined, f: (x: number) => string): string {
  return v === null || v === undefined ? NA : f(v);
}

function tone(v: number | null | undefined): "up" | "down" | undefined {
  return v === null || v === undefined || v === 0 ? undefined : v > 0 ? "up" : "down";
}

function shiftMonth(ym: string, delta: number): string {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function sleeveText(s: string | null | undefined): string {
  return s ? (SLEEVE_TEXT[s] ?? s) : "—";
}

export function PortfolioPage() {
  const { account } = useShell();
  const live = account === "live";
  const base = `/api/portfolio/${account}`;
  const overview = useApi<PortfolioOverview>(`${base}/overview`);
  const perf = useApi<PerformanceOut>(`${base}/performance`);
  const holdings = useApi<HoldingRow[]>(`${base}/holdings`);
  const preview = useApi<RebalancePreview>(`${base}/rebalance-preview`);
  const o = overview.data;

  return (
    <div className="page">
      <Grid>
        <StatCards o={o} live={live} />
      </Grid>
      <Grid>
        <Cell span={8}>
          <PerformancePanel perf={perf} live={live} />
        </Cell>
        <Cell span={4}>
          <AllocationPanel o={o} live={live} />
        </Cell>
      </Grid>
      <Grid>
        <Cell span={12}>
          <HoldingsPanel holdings={holdings} account={account} live={live} />
        </Cell>
      </Grid>
      <Grid>
        <Cell span={4}>
          <ContributionPanel base={base} live={live} />
        </Cell>
        <Cell span={4}>
          <CalendarPanel base={base} asOf={o?.as_of ?? null} live={live} />
        </Cell>
        <Cell span={4}>
          <RiskPanel o={o} live={live} />
        </Cell>
      </Grid>
      <Grid>
        <Cell span={6}>
          <PreviewPanel preview={preview} live={live} />
        </Cell>
        <Cell span={6}>
          <TradesPanel base={base} live={live} />
        </Cell>
      </Grid>
    </div>
  );
}

function StatCards({ o, live }: { o: PortfolioOverview | null; live: boolean }) {
  const s = o?.summary;
  const t = o?.timing;
  const cards = [
    {
      label: "净值",
      value: o?.net_liquidation != null ? money(o.net_liquidation) : NA,
      sub: s?.today_pnl != null ? `今日 ${signedMoney(s.today_pnl)} (${signedPct(s.today_return)})` : `今日 ${NA}`,
      tone: undefined,
    },
    {
      label: "累计收益",
      value: orNA(s?.total_return, (v) => signedPct(v)),
      sub: `QQQ ${orNA(s?.qqq_return, (v) => signedPct(v))} · SPY ${orNA(s?.spy_return, (v) => signedPct(v))}`,
      tone: tone(s?.total_return),
    },
    { label: "最大回撤", value: orNA(s?.max_drawdown, (v) => pct(v)), sub: `当前 ${orNA(s?.current_drawdown, (v) => pct(v))}` },
    { label: "年化波动 / 夏普", value: orNA(s?.ann_vol, (v) => pct(v, 1)), sub: `夏普 ${orNA(s?.sharpe, (v) => num(v))}` },
    { label: "仓位", value: orNA(o?.exposure, (v) => pct(v, 1)), sub: `现金 ${o?.cash != null ? money(o.cash) : NA}` },
    {
      label: "择时系数",
      value: t ? num(t.factor, 2) : "暂无数据",
      sub: t ? `${t.vol_symbol} 20 日波动 ${orNA(t.vol_20d, (v) => pct(v, 0))} / 阈值 ${pct(t.threshold, 0)}` : undefined,
    },
  ];
  return (
    <>
      {cards.map((c) => (
        <Cell key={c.label} span={2}>
          <StatCard label={c.label} value={c.value} sub={c.sub} tone={c.tone} live={live} />
        </Cell>
      ))}
    </>
  );
}

function PerformancePanel({ perf, live }: { perf: ReturnType<typeof useApi<PerformanceOut>>; live: boolean }) {
  const [range, setRange] = useState<RangeKey>("all");
  const [unit, setUnit] = useState<"ret" | "amt">("ret");
  const points = useMemo(() => {
    const all = perf.data?.points ?? [];
    const days = RANGES.find((r) => r.key === range)?.days ?? 0;
    if (!days || all.length === 0) return all;
    const end = new Date(all[all.length - 1].date).getTime();
    return all.filter((p) => end - new Date(p.date).getTime() <= days * 86400000);
  }, [perf.data, range]);
  const series = useMemo(() => {
    if (points.length === 0) return [];
    const f = points[0];
    const rebase = (v: number | null | undefined, b: number | null | undefined) => (v == null || !b ? null : v / b);
    const line = (name: string, color: string, dash: boolean, get: (p: (typeof points)[number]) => number | null) => ({
      name, color, dash,
      points: points.flatMap((p) => { const v = get(p); return v === null ? [] : [{ time: p.date, value: v }]; }),
    });
    if (unit === "amt") return [line("组合", "#2962ff", false, (p) => p.nav)];
    return [
      line("组合", "#2962ff", false, (p) => rebase(p.portfolio, f.portfolio)),
      line("QQQ", "#a855f7", true, (p) => rebase(p.qqq, f.qqq)),
      line("SPY", "#8a8f9c", true, (p) => rebase(p.spy, f.spy)),
    ];
  }, [points, unit]);
  const dd = useMemo(() => [{ name: "回撤", color: "#ef4444", points: points.map((p) => ({ time: p.date, value: p.drawdown })) }], [points]);
  return (
    <Panel
      title="净值走势"
      live={live}
      height={ROW2_H}
      right={
        <>
          <Tabs tabs={RANGES.map((r) => ({ key: r.key, label: r.label }))} value={range} onChange={setRange} />
          <Tabs tabs={[{ key: "ret", label: "收益率" }, { key: "amt", label: "金额" }]} value={unit} onChange={setUnit} />
        </>
      }
    >
      <ApiState result={perf} emptyWhen={(d) => d.points.length === 0}>
        {(d) => (
          <>
            <LineChart height={230} series={series} markers={d.rebalance_dates} />
            <LineChart height={90} series={dd} percent />
          </>
        )}
      </ApiState>
    </Panel>
  );
}

function AllocationPanel({ o, live }: { o: PortfolioOverview | null; live: boolean }) {
  const alloc = o?.allocation ?? [];
  const t = o?.timing;
  return (
    <Panel title="分仓与择时" live={live} height={ROW2_H}>
      {alloc.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="alloc">
          <div className="alloc-top">
            <Donut parts={alloc.map((a) => ({ label: a.sleeve, value: a.actual, color: SLEEVE_COLOR[a.sleeve] ?? "#666" }))} />
            <table className="dt compact">
              <thead><tr><th>子仓</th><th className="r">实际</th><th className="r">目标</th></tr></thead>
              <tbody>
                {alloc.map((a) => (
                  <tr key={a.sleeve}>
                    <td><span className="dot" style={{ background: SLEEVE_COLOR[a.sleeve] }} />{sleeveText(a.sleeve)}</td>
                    <td className="r num">{pct(a.actual, 1)}</td>
                    <td className="r num">{a.target == null ? "—" : pct(a.target, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sm">
            {t
              ? `择时：${t.vol_symbol} 20 日年化波动 ${orNA(t.vol_20d, (v) => pct(v, 1))}，阈值 ${pct(t.threshold, 0)}${t.vol_20d != null ? `，距阈值 ${signedPct(t.vol_20d - t.threshold, 1)}` : ""}`
              : "择时：暂无数据"}
          </div>
          <div className="sm muted">按板块分布</div>
          <EmptyState kind="pending" />
        </div>
      )}
    </Panel>
  );
}

function HoldingsPanel({ holdings, account, live }: { holdings: ReturnType<typeof useApi<HoldingRow[]>>; account: string; live: boolean }) {
  const [filter, setFilter] = useState<"all" | "swing" | "momentum">("all");
  return (
    <Panel
      title={`持仓${holdings.data ? ` · ${holdings.data.length} 只` : ""}`}
      live={live}
      right={<Tabs tabs={[{ key: "all", label: "全部" }, { key: "swing", label: "波段仓" }, { key: "momentum", label: "动量仓" }]} value={filter} onChange={setFilter} />}
    >
      <ApiState result={holdings}>
        {(rows) => (
          <DataTable<HoldingRow>
            maxHeight={420}
            rowKey={(r) => r.symbol}
            rows={rows.filter((r) => filter === "all" || r.sleeve === filter || r.sleeve === "both")}
            columns={[
              { key: "symbol", label: "代码", render: (r) => <Link to={{ pathname: `/stock/${r.symbol}`, search: `?account=${account}` }}>{r.symbol}</Link> },
              { key: "sleeve", label: "所属仓", render: (r) => sleeveText(r.sleeve) },
              { key: "qty", label: "数量", align: "right", render: (r) => num(r.qty, 0) },
              { key: "avg_cost", label: "成本", align: "right", render: (r) => num(r.avg_cost) },
              { key: "price", label: "现价", align: "right", render: (r) => (r.price == null ? "—" : num(r.price)) },
              { key: "change_pct", label: "今日", align: "right", render: (r) => <span className={tone(r.change_pct)}>{r.change_pct == null ? "—" : signedPct(r.change_pct)}</span> },
              { key: "market_value", label: "市值", align: "right", render: (r) => money(r.market_value) },
              { key: "weight", label: "实际 / 目标", align: "right", render: (r) => `${pct(r.weight, 1)} / ${pct(r.target_weight, 1)}` },
              { key: "deviation", label: "偏差", render: (r) => <DeviationBar value={r.deviation ?? null} /> },
              { key: "unrealized_pnl", label: "浮动盈亏", align: "right", render: (r) => <span className={tone(r.unrealized_pnl)}>{r.unrealized_pnl == null ? "—" : signedMoney(r.unrealized_pnl)}</span> },
              { key: "state", label: "信号状态", render: (r) => (r.state ? <span><span className={`pill ${STATE_CLASS[r.state]}`}>●</span> {r.state_note}</span> : "—") },
              { key: "next_earnings", label: "下次财报", render: (r) => r.next_earnings ?? "—" },
              { key: "holding_days", label: "持有天数", align: "right", render: (r) => r.holding_days },
            ]}
          />
        )}
      </ApiState>
    </Panel>
  );
}

function ContributionPanel({ base, live }: { base: string; live: boolean }) {
  const [period, setPeriod] = useState<"day" | "month">("day");
  const [bySleeve, setBySleeve] = useState(false);
  const res = useApi<ContributionRow[]>(`${base}/contributions`, { period });
  return (
    <Panel
      title="收益贡献"
      live={live}
      height={ROW4_H}
      scroll
      right={
        <>
          <Tabs tabs={[{ key: "day", label: "今日" }, { key: "month", label: "本月" }]} value={period} onChange={setPeriod} />
          <Tabs tabs={[{ key: "sym", label: "个股" }, { key: "sleeve", label: "按子仓" }]} value={bySleeve ? "sleeve" : "sym"} onChange={(k) => setBySleeve(k === "sleeve")} />
        </>
      }
    >
      <ApiState result={res}>
        {(rows) => {
          const items = bySleeve
            ? Object.entries(rows.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.sleeve ?? "other"]: (acc[r.sleeve ?? "other"] ?? 0) + r.pnl }), {}))
                .map(([k, v]) => ({ key: k, label: sleeveText(k), pnl: v }))
            : rows.map((r, i) => ({ key: `${r.symbol}-${r.sleeve}-${i}`, label: r.symbol, pnl: r.pnl }));
          const maxAbs = Math.max(1, ...items.map((x) => Math.abs(x.pnl)));
          return (
            <div className="bars">
              {items.map((x) => (
                <div key={x.key} className="bar-row">
                  <span className="lbl">{x.label}</span>
                  <span className="bar"><span className={x.pnl >= 0 ? "pos" : "neg"} style={{ width: `${(Math.abs(x.pnl) / maxAbs) * 100}%` }} /></span>
                  <span className={`num ${tone(x.pnl) ?? ""}`}>{signedMoney(x.pnl)}</span>
                </div>
              ))}
            </div>
          );
        }}
      </ApiState>
    </Panel>
  );
}

function CalendarPanel({ base, asOf, live }: { base: string; asOf: string | null; live: boolean }) {
  const [month, setMonth] = useState<string | null>(null);
  const shown = month ?? asOf?.slice(0, 7) ?? null;
  const res = useApi<CalendarDayOut[]>(`${base}/calendar`, month ? { month } : undefined);
  return (
    <Panel title="每日盈亏" live={live} height={ROW4_H}>
      {shown === null ? (
        <EmptyState />
      ) : (
        <ApiState result={res} emptyWhen={() => false}>
          {(days) => (
            <PnlCalendar
              year={Number(shown.slice(0, 4))}
              month={Number(shown.slice(5, 7))}
              days={days}
              onPrev={() => setMonth(shiftMonth(shown, -1))}
              onNext={() => setMonth(shiftMonth(shown, 1))}
            />
          )}
        </ApiState>
      )}
    </Panel>
  );
}

function RiskPanel({ o, live }: { o: PortfolioOverview | null; live: boolean }) {
  const r = o?.risk;
  const rows: [string, string][] = [
    ["Beta（对 SPY）", orNA(r?.beta_spy, (v) => num(v))],
    ["与 QQQ 相关性", orNA(r?.corr_qqq, (v) => num(v))],
    ["1 日 VaR 95%", orNA(r?.var95_1d, (v) => money(v))],
    ["前 5 集中度", orNA(o?.top5, (v) => pct(v, 1))],
    ["最大单一持仓", orNA(o?.max_single, (v) => pct(v, 1))],
    ["下周有财报", o ? (o.earnings_next_week.length ? o.earnings_next_week.join("、") : "无") : NA],
    ["熔断开关", o ? (o.reconcile.trading_enabled ? "开启" : "关闭（禁止下单）") : NA],
    ["最近日报 / 对账", o?.reconcile.last_report_status ?? "暂无"],
  ];
  return (
    <Panel title="风险 · 近 60 个交易日" live={live} height={ROW4_H}>
      <table className="dt compact kv">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}><td className="muted">{k}</td><td className="r num">{v}</td></tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function PreviewPanel({ preview, live }: { preview: ReturnType<typeof useApi<RebalancePreview>>; live: boolean }) {
  const p = preview.data;
  return (
    <Panel title="下次调仓预览" live={live} height={ROW5_H} right={p?.signal_date ? <span className="muted sm">基于 {p.signal_date} 收盘信号</span> : undefined}>
      <div className="sm muted" style={{ marginBottom: 6 }}>只读预览，面板不能下单</div>
      <ApiState result={preview} emptyWhen={(d) => d.rows.length === 0}>
        {(d) => (
          <>
            <DataTable<RebalancePreview["rows"][number]>
              maxHeight={ROW5_H - 130}
              rowKey={(r) => `${r.side}-${r.symbol}`}
              rows={d.rows}
              columns={[
                { key: "side", label: "方向", render: (r) => <span className={`pill ${r.side === "BUY" ? "ok" : "err"}`}>{r.side === "BUY" ? "买入" : "卖出"}</span> },
                { key: "symbol", label: "代码" },
                { key: "sleeve", label: "子仓", render: (r) => sleeveText(r.sleeve) },
                { key: "w", label: "权重变化", align: "right", render: (r) => `${pct(r.weight_from, 1)} → ${pct(r.weight_to, 1)}` },
                { key: "reason", label: "原因" },
              ]}
            />
            <div className="sm muted" style={{ marginTop: 6 }}>
              预计换手 {pct(d.turnover, 1)} · 佣金（估算）{d.est_commission == null ? "—" : money(d.est_commission)}
            </div>
          </>
        )}
      </ApiState>
    </Panel>
  );
}

function TradesPanel({ base, live }: { base: string; live: boolean }) {
  const [tab, setTab] = useState<"orders" | "fills" | "closed">("orders");
  const orders = useApi<OrderOut[]>(tab === "orders" ? `${base}/orders` : null);
  const fills = useApi<FillOut[]>(tab === "fills" ? `${base}/fills` : null);
  const closed = useApi<ClosedTradesOut>(tab === "closed" ? `${base}/closed-trades` : null);
  const h = ROW5_H - 60;
  return (
    <Panel
      title="订单与成交"
      live={live}
      height={ROW5_H}
      right={<Tabs tabs={[{ key: "orders", label: "订单" }, { key: "fills", label: "成交" }, { key: "closed", label: "已平仓" }]} value={tab} onChange={setTab} />}
    >
      {tab === "orders" && (
        <ApiState result={orders}>
          {(rows) => (
            <DataTable<OrderOut>
              maxHeight={h}
              rowKey={(r) => r.client_order_id}
              rows={rows}
              columns={[
                { key: "created_at", label: "时间 (ET)", render: (r) => formatDateTimeET(r.created_at) },
                { key: "symbol", label: "代码" },
                { key: "side", label: "方向", render: (r) => <span className={`pill ${r.side === "BUY" ? "ok" : "err"}`}>{r.side === "BUY" ? "买入" : "卖出"}</span> },
                { key: "qty", label: "数量", align: "right" },
                { key: "status", label: "状态" },
                { key: "avg_fill_price", label: "成交均价", align: "right", render: (r) => num(r.avg_fill_price) },
                { key: "reason", label: "原因" },
              ]}
            />
          )}
        </ApiState>
      )}
      {tab === "fills" && (
        <ApiState result={fills}>
          {(rows) => (
            <DataTable<FillOut>
              maxHeight={h}
              rowKey={(r, i) => `${r.filled_at}-${r.symbol}-${i}`}
              rows={rows}
              columns={[
                { key: "filled_at", label: "时间 (ET)", render: (r) => formatDateTimeET(r.filled_at) },
                { key: "symbol", label: "代码" },
                { key: "side", label: "方向", render: (r) => (r.side === "BUY" ? "买入" : "卖出") },
                { key: "qty", label: "数量", align: "right" },
                { key: "price", label: "价格", align: "right", render: (r) => num(r.price) },
                { key: "commission", label: "佣金", align: "right", render: (r) => num(r.commission) },
              ]}
            />
          )}
        </ApiState>
      )}
      {tab === "closed" && (
        <ApiState result={closed} emptyWhen={(d) => d.trades.length === 0}>
          {(d) => (
            <>
              <div className="sm" style={{ marginBottom: 6 }}>
                <span>胜率 {pct(d.stats.win_rate, 1)}</span>
                <span className="muted"> · 平均盈亏 {d.stats.avg_pnl == null ? "—" : signedMoney(d.stats.avg_pnl)} · 平均滑点 {pct(d.stats.avg_slippage, 2)}</span>
              </div>
              <DataTable<ClosedTradeOut>
                maxHeight={h - 30}
                rowKey={(r, i) => `${r.symbol}-${r.close_date}-${i}`}
                rows={d.trades}
                columns={[
                  { key: "symbol", label: "代码" },
                  { key: "open_date", label: "开仓" },
                  { key: "close_date", label: "平仓" },
                  { key: "days", label: "天数", align: "right" },
                  { key: "qty", label: "数量", align: "right" },
                  { key: "ret", label: "收益", align: "right", render: (r) => <span className={tone(r.ret)}>{signedPct(r.ret)}</span> },
                  { key: "pnl", label: "盈亏", align: "right", render: (r) => <span className={tone(r.pnl)}>{signedMoney(r.pnl)}</span> },
                ]}
              />
            </>
          )}
        </ApiState>
      )}
    </Panel>
  );
}
