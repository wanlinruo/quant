import { useMemo, useState } from "react";
import { Link } from "react-router";
import type {
  AccountPoint,
  BreadthOut,
  HoldingRow,
  JobStatusOut,
  MarketOverview,
  MoverOut,
  PortfolioOverview,
  SignalOut,
  StatOut,
  TickerItemOut,
} from "../api/types";
import { ApiState } from "../components/ApiState";
import { CalendarPanel } from "../components/CalendarPanel";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { NewsPanel } from "../components/NewsPanel";
import { Panel } from "../components/Panel";
import { Sparkline } from "../components/Sparkline";
import { Tabs } from "../components/Tabs";
import { Treemap, type TreeGroup } from "../components/Treemap";
import { CandleChart } from "../charts/CandleChart";
import { LineChart } from "../charts/LineChart";
import { useApi } from "../hooks/useApi";
import { compact, money, num, pct, signedMoney, signedPct } from "../lib/format";
import { etParts } from "../lib/time";
import { useShell } from "../shell";

// 行情条 10 项的固定顺序：ETF 代理来自 market_data 快照，宏观项来自 market_extras（FRED / Coinbase）
const STRIP_ORDER = [
  ["SPY", "标普 500"], ["QQQ", "纳指 100"], ["DIA", "道指"], ["IWM", "罗素 2000"], ["VIX", "VIX"],
  ["US10Y", "美债 10 年"], ["USD", "美元指数"], ["WTI", "原油"], ["GLD", "黄金"], ["BTC", "比特币"],
] as const;
const MACRO_KEYS = new Set(["VIX", "US10Y", "USD", "WTI", "BTC"]);
const PERIODS = [
  { key: "1D", label: "1日" },
  { key: "1W", label: "1周" },
  { key: "1M", label: "1月" },
  { key: "YTD", label: "YTD" },
] as const;
type PeriodKey = (typeof PERIODS)[number]["key"];
const FILTERS = [
  { key: "all", label: "综合" },
  { key: "up", label: "涨幅" },
  { key: "down", label: "跌幅" },
  { key: "volume", label: "放量" },
  { key: "gap", label: "跳空" },
  { key: "hilo", label: "新高低" },
  { key: "mine", label: "我的持仓" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];
const INDICES = ["SPY", "QQQ", "DIA", "IWM"] as const;
type IndexKey = (typeof INDICES)[number];
const INDEX_RANGES = [{ key: "1M", label: "1月", days: 22 }, { key: "3M", label: "3月", days: 66 }, { key: "1Y", label: "1年", days: 252 }] as const;
type IndexRange = (typeof INDEX_RANGES)[number]["key"];
const HEAT_H = 470;
const SIDE_H = 380;
const ROW_H = 360;

function StripItem({ t, asOf }: { t: TickerItemOut; asOf: string | null | undefined }) {
  const bp = t.unit === "yield";
  const chg = bp ? (t.change == null ? "—" : `${t.change >= 0 ? "+" : ""}${Math.round(t.change * 100)}bp`) : signedPct(t.change_pct);
  const stale = t.as_of && (MACRO_KEYS.has(t.symbol) || (asOf && t.as_of !== asOf)) ? `截至 ${t.as_of.slice(5)}` : null;
  return (
    <>
      <div className="muted sm">
        <span className="strip-label">{t.label}</span>
        <span className="etf"> {MACRO_KEYS.has(t.symbol) ? t.note : t.symbol}</span>
      </div>
      <div className="num strip-v">{num(t.close)}{bp ? "%" : ""}</div>
      <div className={`num sm ${tone(bp ? t.change : t.change_pct)}`}>
        {chg}
        {stale && <span className="muted"> {stale}</span>}
      </div>
      <Sparkline values={t.spark} width={70} height={18} />
    </>
  );
}

function tone(v: number | null | undefined): string {
  return v == null || v === 0 ? "" : v > 0 ? "up" : "down";
}

/** 涨跌着色：±3% 饱和，绿涨红跌。 */
function heatColor(r: number | null | undefined): string {
  if (r == null) return "#2a2f3b";
  const a = Math.min(Math.abs(r) / 0.03, 1);
  return r >= 0 ? `rgba(34,197,94,${0.15 + 0.75 * a})` : `rgba(239,68,68,${0.15 + 0.75 * a})`;
}

const MOVER_MIN_CHANGE = 0.02;

function filterMovers(all: MoverOut[], f: FilterKey, mine: Set<string>): MoverOut[] {
  if (f === "mine") return all.filter((m) => mine.has(m.symbol));
  // 持仓标的不论涨跌都在表里；其他筛选只看达到门槛或有信号的
  const rows = all.filter((m) => !m.held || Math.abs(m.change_pct) >= MOVER_MIN_CHANGE || m.signals.length > 0);
  switch (f) {
    case "up":
      return rows.filter((m) => m.change_pct > 0).sort((a, b) => b.change_pct - a.change_pct);
    case "down":
      return rows.filter((m) => m.change_pct < 0).sort((a, b) => a.change_pct - b.change_pct);
    case "volume":
      return rows.filter((m) => m.signals.includes("放量突破") || (m.volume_ratio ?? 0) >= 2);
    case "gap":
      return rows.filter((m) => m.signals.includes("跳空"));
    case "hilo":
      return rows.filter((m) => m.signals.some((s) => s.startsWith("52周")));
    default:
      return rows;
  }
}

type Full = Required<Omit<MarketOverview, "breadth" | "as_of" | "generated_at" | "coverage" | "intraday_time" | "intraday_time_start" | "session" | "quote_delayed">> &
  Pick<MarketOverview, "breadth" | "as_of" | "generated_at" | "coverage" | "intraday_time">;

/** 快照有数据时补齐可选字段；pending 或未加载时为 null。 */
function full(m: MarketOverview | null): Full | null {
  if (!m || m.status !== "ok") return null;
  return {
    status: "ok", as_of: m.as_of, stale: m.stale ?? false, generated_at: m.generated_at, members: m.members ?? 0,
    coverage: m.coverage, strip: m.strip ?? [], highlights: m.highlights ?? "", breadth: m.breadth,
    styles: m.styles ?? [], sectors: m.sectors ?? [], movers: m.movers ?? [], heatmap: m.heatmap ?? {},
    index_candles: m.index_candles ?? {}, macro: m.macro ?? [], sector_periods: m.sector_periods ?? {},
    intraday: m.intraday ?? false, intraday_time: m.intraday_time,
  };
}

/** 市场首页（设计 5.1，原型 v4）：盘后复盘，数据来自 market_data 任务的快照。 */
const OVERVIEW_REFRESH_MS = import.meta.env.MODE === "test" ? 200 : 3 * 60_000;

export function MarketPage() {
  const { account, market: sharedMarket } = useShell();
  const fallback = useApi<MarketOverview>(sharedMarket ? null : "/api/market/overview", undefined, OVERVIEW_REFRESH_MS);
  const market = sharedMarket ?? fallback;
  const holdings = useApi<HoldingRow[]>(`/api/portfolio/${account}/holdings`);
  const m = useMemo(() => full(market.data), [market.data]);
  const mine = useMemo(() => new Set((holdings.data ?? []).map((h) => h.symbol)), [holdings.data]);
  const link = (sym: string) => ({ pathname: `/stock/${sym}`, search: `?account=${account}` });
  const stripItems = useMemo(
    () => new Map([...(m?.strip ?? []), ...(market.data?.macro ?? [])].map((t) => [t.symbol, t])),
    [m, market.data],
  );

  return (
    <div className="page">
      {market.error && <div className="error-box" role="alert">市场概览请求失败：{market.error.message}</div>}
      {m && (m.stale || (m.coverage != null && m.coverage < 0.95)) && (
        <div className="error-box" role="alert">
          市场日线尚未完整就绪{m.coverage != null ? `，成分覆盖 ${pct(m.coverage, 1)}` : ""}；当前展示保留快照，后台将继续重试。
        </div>
      )}
      <Grid>
        <Cell span={12}>
          <section className="panel strip" data-testid="ticker-strip">
            {STRIP_ORDER.map(([sym, label]) => {
              const t = stripItems.get(sym);
              if (!t)
                return (
                  <div key={sym} className="strip-item">
                    <div className="muted sm"><span className="strip-label">{label}</span></div>
                    <div className="muted sm">数据接入中</div>
                  </div>
                );
              return MACRO_KEYS.has(sym) ? (
                <div key={sym} className="strip-item"><StripItem t={t} asOf={m?.as_of} /></div>
              ) : (
                <Link key={sym} to={link(sym)} className="strip-item"><StripItem t={t} asOf={m?.as_of} /></Link>
              );
            })}
          </section>
        </Cell>
      </Grid>
      <Grid>
        <Cell span={9}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Grid>
              <Cell span={8}>
                <Heatmap m={m} link={link} />
              </Cell>
              <Cell span={4}>
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <BreadthPanel b={m?.breadth ?? null} />
                  <Panel title="风格与因子" right={<span className="muted sm">1日 / 1月</span>} grow>
                    {m && m.styles.length ? (
                      <table className="dt compact kv">
                        <tbody>
                          {m.styles.map((s) => (
                            <tr key={s.key}>
                              <td className="muted">{s.label}</td>
                              <td className={`r num ${tone(s.d1)}`}>{signedPct(s.d1)}</td>
                              <td className={`r num ${tone(s.m1)}`}>{signedPct(s.m1)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <EmptyState kind="pending" />
                    )}
                  </Panel>
                </div>
              </Cell>
            </Grid>
            <Movers rows={m ? m.movers : null} mine={mine} link={link} />
            <Grid>
              <Cell span={6}>
                <IndexChart m={m} />
              </Cell>
              <Cell span={6}>
                <Panel title="板块表现" right={<span className="muted sm">SPDR 板块 ETF · 价格涨跌，不含分红 · 按 1日 排序</span>} height={ROW_H}>
                  {m && m.sectors.length ? (
                    <div className="table-wrap" style={{ maxHeight: ROW_H - 60 }}>
                      <table className="dt">
                        <thead>
                          <tr><th>板块</th><th className="r">1日</th><th className="r">1周</th><th className="r">1月</th><th className="r">YTD</th><th className="r">相对标普</th><th>1月走势</th></tr>
                        </thead>
                        <tbody>
                          {m.sectors.map((s) => (
                            <tr key={s.symbol}>
                              <td>{s.name} <span className="muted sm">{s.symbol}{s.as_of && m.as_of && s.as_of !== m.as_of ? ` · 截至 ${s.as_of.slice(5)}` : ""}</span></td>
                              <td className={`r num ${tone(s.d1)}`}>{signedPct(s.d1)}</td>
                              <td className={`r num ${tone(s.w1)}`}>{signedPct(s.w1)}</td>
                              <td className={`r num ${tone(s.m1)}`}>{signedPct(s.m1)}</td>
                              <td className={`r num ${tone(s.ytd)}`}>{signedPct(s.ytd)}</td>
                              <td className={`r num ${tone(s.rel_spy_d1)}`}>{signedPct(s.rel_spy_d1)}</td>
                              <td><Sparkline values={s.spark} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <EmptyState kind="pending" />
                  )}
                </Panel>
              </Cell>
            </Grid>
          </div>
        </Cell>
        <Cell span={3}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <NewsPanel height={SIDE_H} mine={mine} />
            <CalendarPanel height={SIDE_H - 20} />
            <MyStrategy />
          </div>
        </Cell>
      </Grid>
    </div>
  );
}

function Heatmap({ m, link }: { m: Full | null; link: (s: string) => { pathname: string; search: string } }) {
  const [period, setPeriod] = useState<PeriodKey>("1D");
  const groups = useMemo<TreeGroup[]>(() => {
    const by = new Map<string, TreeGroup>();
    for (const c of m?.heatmap[period] ?? []) {
      const key = c.sector ?? "其他";
      if (!by.has(key)) by.set(key, { key, label: key, cells: [] });
      by.get(key)!.cells.push({
        key: c.symbol,
        label: c.symbol,
        sublabel: signedPct(c.ret, 1),
        value: c.market_cap,
        color: heatColor(c.ret),
        title: `${c.symbol} ${c.name ?? ""} ${signedPct(c.ret, 2)} 市值 ${compact(c.market_cap)}`,
        to: link(c.symbol),
      });
    }
    return [...by.values()];
  }, [m, period, link]);
  return (
    <Panel
      title="标普 500 热力图"
      right={<Tabs tabs={PERIODS.map((p) => ({ key: p.key, label: p.label }))} value={period} onChange={setPeriod} />}
      grow
    >
      <div data-testid="heatmap">
        {groups.length ? <Treemap groups={groups} width={800} height={HEAT_H - 20} /> : <EmptyState kind="pending" note="市值每天补 60 只，约 9 个交易日补齐" />}
      </div>
    </Panel>
  );
}

function statRow(label: string, s: StatOut | undefined, fmt: (v: number) => string) {
  return (
    <tr key={label}>
      <td className="muted">{label}</td>
      <td className="r num">{s?.value == null ? "—" : fmt(s.value)}</td>
      <td className="r num muted">{s?.prev == null ? "—" : fmt(s.prev)}</td>
    </tr>
  );
}

function BreadthPanel({ b }: { b: BreadthOut | null }) {
  const n = (v: number) => num(v, 0);
  const p = (v: number) => pct(v, 0);
  return (
    <Panel title="市场宽度" right={<span className="muted sm">标普 500 成分股 · 今日 / 前日</span>}>
      {b ? (
        <table className="dt compact kv">
          <tbody>
            {statRow("上涨", b.advancers, n)}
            {statRow("下跌", b.decliners, n)}
            {statRow("持平", b.unchanged, n)}
            {statRow("52 周新高", b.new_highs, n)}
            {statRow("52 周新低", b.new_lows, n)}
            {statRow("站上 50 日线", b.above_50, p)}
            {statRow("站上 200 日线", b.above_200, p)}
            {statRow("上涨成交量占比", b.up_volume_ratio, p)}
          </tbody>
        </table>
      ) : (
        <EmptyState kind="pending" />
      )}
    </Panel>
  );
}

function Movers({ rows, mine, link }: { rows: MoverOut[] | null; mine: Set<string>; link: (s: string) => { pathname: string; search: string } }) {
  const [f, setF] = useState<FilterKey>("all");
  const shown = rows ? filterMovers(rows, f, mine) : [];
  return (
    <Panel title="异动个股" right={<Tabs tabs={FILTERS.map((x) => ({ key: x.key, label: x.label }))} value={f} onChange={setF} />} height={ROW_H}>
      {rows === null ? (
        <EmptyState kind="pending" />
      ) : (
        <div className="table-wrap" style={{ maxHeight: ROW_H - 60 }} data-testid="movers">
          {shown.length === 0 ? (
            <EmptyState />
          ) : (
            <table className="dt">
              <thead>
                <tr><th>代码</th><th>名称</th><th>板块</th><th className="r">市值</th><th className="r">收盘</th><th className="r">涨跌</th><th className="r">量比</th><th>信号</th><th>原因（关联新闻）</th><th>20 日</th></tr>
              </thead>
              <tbody>
                {shown.map((x) => (
                  <tr key={x.symbol}>
                    <td><Link to={link(x.symbol)}>{x.symbol}</Link></td>
                    <td>{x.name ?? "—"}</td>
                    <td>{x.sector ?? "—"}</td>
                    <td className="r num">{x.market_cap == null ? "—" : compact(x.market_cap)}</td>
                    <td className="r num">{num(x.close)}</td>
                    <td className={`r num ${tone(x.change_pct)}`}>{signedPct(x.change_pct)}</td>
                    <td className="r num">{x.volume_ratio == null ? "—" : num(x.volume_ratio, 1)}</td>
                    <td>{x.signals.map((s) => <span key={s} className="pill info" style={{ marginRight: 4 }}>{s}</span>)}</td>
                    <td className="reason" data-testid="reason">
                      {x.reason ? (
                        <a href={x.reason.url} target="_blank" rel="noreferrer" title={x.reason.headline}>
                          {`${x.reason.headline} · ${x.reason.source} ${etParts(x.reason.time).time}`}
                        </a>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td><Sparkline values={x.spark} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Panel>
  );
}

function IndexChart({ m }: { m: Full | null }) {
  const [sym, setSym] = useState<IndexKey>("SPY");
  const [range, setRange] = useState<IndexRange>("1Y");
  const days = INDEX_RANGES.find((x) => x.key === range)?.days ?? 252;
  const bars = useMemo(
    () => (m?.index_candles[sym] ?? []).slice(-days).map((c) => ({ date: c.date, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume })),
    [days, m, sym],
  );
  return (
    <Panel title="指数日线（ETF）" right={<span style={{ display: "flex", gap: 8 }}><Tabs tabs={INDEX_RANGES.map((x) => ({ key: x.key, label: x.label }))} value={range} onChange={setRange} /><Tabs tabs={INDICES.map((s) => ({ key: s, label: s }))} value={sym} onChange={setSym} /></span>} height={ROW_H}>
      {bars.length ? <CandleChart height={ROW_H - 70} bars={bars} mas={[20, 50]} /> : <EmptyState kind="pending" />}
    </Panel>
  );
}

function MyStrategy() {
  const { account } = useShell();
  const live = account === "live";
  const curve = useApi<AccountPoint[]>(`/api/portfolio/${account}/curve`);
  const overview = useApi<PortfolioOverview>(`/api/portfolio/${account}/overview`);
  const signals = useApi<SignalOut[]>("/api/signals/latest", { mode: account });
  const jobs = useApi<JobStatusOut[]>("/api/system/jobs", { mode: account });
  const o = overview.data;
  return (
    <Panel title="我的策略" live={live} grow>
      <ApiState result={curve}>
        {(pts) => {
          const last = pts.at(-1)!;
          const s = o?.summary;
          return (
            <div>
              <div className="muted sm">账户净值 · {o?.as_of ?? last.date}</div>
              <div className="num" style={{ fontSize: 22, color: "#fff", fontWeight: 700 }}>
                {money(o?.net_liquidation ?? last.net_liquidation)}{" "}
                {s?.today_pnl != null && (
                  <span className={s.today_pnl >= 0 ? "up" : "down"} style={{ fontSize: 13 }}>
                    {signedMoney(s.today_pnl)} ({signedPct(s.today_return)})
                  </span>
                )}
              </div>
              {o?.timing && <div className="muted sm">择时系数 {num(o.timing.factor)} · 仓位 {pct(o.exposure, 1)}</div>}
              <LineChart height={80} series={[{ name: "", color: "#2962ff", points: pts.slice(-30).map((p) => ({ time: p.date, value: p.net_liquidation })) }]} />
            </div>
          );
        }}
      </ApiState>
      <div style={{ marginTop: 8 }}>
        <ApiState result={signals}>
          {(sigs) => (
            <div className="sm">最新信号：{sigs.map((s) => `${s.strategy} · ${s.signal_date} · ${Object.keys(s.target_weights).length} 个标的`).join("；")}</div>
          )}
        </ApiState>
      </div>
      <div style={{ marginTop: 6 }}>
        <ApiState result={jobs}>
          {(js) => (
            <div className="sm">
              最近任务：
              {js.map((j) => (
                <span key={j.job_name} className={`pill ${j.status === "success" ? "ok" : j.status === "failed" ? "err" : "info"}`} style={{ marginRight: 4 }}>{j.job_name}</span>
              ))}
            </div>
          )}
        </ApiState>
      </div>
    </Panel>
  );
}
