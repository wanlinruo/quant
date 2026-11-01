import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import type { BarsOut, StockProfile } from "../api/types";
import { ApiState } from "../components/ApiState";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { RangeBar } from "../components/RangeBar";
import { Tabs } from "../components/Tabs";
import { CandleChart, type TradeMark } from "../charts/CandleChart";
import { useApi } from "../hooks/useApi";
import { compact, num, pct, signedPct } from "../lib/format";
import { formatDateTimeET, toETDate } from "../lib/time";
import { useShell } from "../shell";

const YEARS = [1, 2, 3, 5, 10, 20];
const MAS = [10, 20, 120];
const PERIOD_KEYS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y"] as const;
const PERIOD_TEXT: Record<string, string> = { "1D": "1日", "1W": "1周", "1M": "1月", "3M": "3月", "6M": "6月", YTD: "今年", "1Y": "1年" };
const SLEEVE_TEXT: Record<string, string> = { momentum: "动量仓", swing: "波段仓", both: "波段+动量", exit: "待卖出" };

function startOf(years: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

function tone(v: number | null | undefined): string {
  return v == null || v === 0 ? "" : v > 0 ? "up" : "down";
}

function sp(v: number | null | undefined, digits = 2): string {
  return v == null ? "—" : signedPct(v, digits);
}

function yesNo(v: boolean | null | undefined, yes: string, no: string): ReactNode {
  if (v == null) return <span className="muted">—</span>;
  return <span className={`pill ${v ? "ok" : "err"}`}>{v ? yes : no}</span>;
}

/** 个股详情页（设计 5.4）：报价头部 + 区间表现 + 日线 + 我的策略；右栏关键数据与财报。 */
export function StockPage() {
  const symbol = (useParams().symbol ?? "").toUpperCase();
  const { account } = useShell();
  const navigate = useNavigate();
  const [years, setYears] = useState(2);
  const [adjust, setAdjust] = useState(false);
  const [tab, setTab] = useState<"strategy" | "news" | "earnings" | "backtest">("strategy");
  const [focus, setFocus] = useState<string | null>(null);
  const start = useMemo(() => startOf(years), [years]);
  const res = useApi<BarsOut>(`/api/market/bars/${encodeURIComponent(symbol)}`, { start });
  const prof = useApi<StockProfile>(`/api/stock/${encodeURIComponent(symbol)}/profile`, { mode: account });
  const p = prof.data;
  const list = res.data?.bars;

  const candles = useMemo(
    () =>
      (list ?? []).map((b) => {
        const f = adjust && b.close ? b.adj_close / b.close : 1;
        return { date: b.date, open: b.open * f, high: b.high * f, low: b.low * f, close: b.close * f, volume: b.volume };
      }),
    [list, adjust],
  );
  const trades = useMemo<TradeMark[]>(
    () => (p?.trades ?? []).map((t) => ({ date: toETDate(t.filled_at), side: t.side === "BUY" ? "BUY" : "SELL" })),
    [p],
  );
  const earningsDates = useMemo(() => [...(p?.earnings.history.map((e) => e.date) ?? []), ...(p?.earnings.next ? [p.earnings.next] : [])], [p]);
  const periods = useMemo(() => p?.holding_periods ?? [], [p]);
  const last = list?.at(-1);
  const prev = list && list.length > 1 ? list.at(-2) : undefined;
  const q = p?.quote;
  const rel = p?.relation;
  const link = (sym: string) => ({ pathname: `/stock/${sym}`, search: `?account=${account}` });

  return (
    <div className="page">
      <Grid>
        <Cell span={12}>
          <section className="panel">
            <div className="quote-h">
              <button type="button" className="btn2" onClick={() => navigate(-1)}>返回</button>
              <h1 style={{ margin: 0, fontSize: 22, color: "#fff" }}>{symbol}</h1>
              {last && (
                <>
                  <span className="num" style={{ fontSize: 26, color: "#fff", fontWeight: 700 }}>{num(last.close)}</span>
                  {prev && <span className={`num ${tone(last.close - prev.close)}`} style={{ fontSize: 15 }}>{signedPct(last.close / prev.close - 1)}</span>}
                  <span className="muted sm">{last.date} 收盘</span>
                </>
              )}
              {rel && (
                <span className="chips-row">
                  <span className={`chip2${rel.held ? " on" : ""}`}>
                    {rel.held ? `持有中 · ${rel.sleeve ? SLEEVE_TEXT[rel.sleeve] ?? rel.sleeve : "—"} · ${pct(rel.weight, 1)}` : "未持有"}
                  </span>
                  {rel.momentum_rank != null && <span className="chip2">动量排名 {rel.momentum_rank} / {rel.universe_size ?? "—"}</span>}
                  {rel.in_sp500 && <span className="chip2">标普 500 成分</span>}
                  {rel.swing_universe && <span className="chip2">波段股票池</span>}
                </span>
              )}
              <span style={{ flex: 1 }} />
              {rel?.prev_holding && <Link to={link(rel.prev_holding)}>‹ 上一只 {rel.prev_holding}</Link>}
              {rel?.next_holding && <Link to={link(rel.next_holding)}>下一只 {rel.next_holding} ›</Link>}
            </div>
            {q && (
              <div className="quote-stats muted" style={{ marginTop: 8 }}>
                <span>开 <b className="num">{num(q.open)}</b></span>
                <span>高 <b className="num">{num(q.high)}</b></span>
                <span>低 <b className="num">{num(q.low)}</b></span>
                <span>量 <b className="num">{compact(q.volume)}</b></span>
                <span>额 <b className="num">${compact(q.turnover)}</b></span>
                <span>52 周 <b className="num">{num(q.low_52w)} – {num(q.high_52w)}</b></span>
                <span>1 月 <b className={`num ${tone(q.ret_1m)}`}>{sp(q.ret_1m)}</b></span>
                <span>今年 <b className={`num ${tone(q.ytd)}`}>{sp(q.ytd)}</b></span>
              </div>
            )}
          </section>
        </Cell>
      </Grid>
      <Grid>
        <Cell span={9}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Panel title="区间表现" right={<span className="muted sm">对比纳指 100（QQQ）</span>}>
              {p && Object.keys(p.periods).length ? (
                <table className="dt compact">
                  <thead>
                    <tr><th />{PERIOD_KEYS.map((k) => <th key={k} className="r">{PERIOD_TEXT[k]}</th>)}</tr>
                  </thead>
                  <tbody>
                    <tr><td>{symbol}</td>{PERIOD_KEYS.map((k) => <td key={k} className={`r num ${tone(p.periods[k])}`}>{sp(p.periods[k])}</td>)}</tr>
                    <tr><td className="muted">QQQ</td>{PERIOD_KEYS.map((k) => <td key={k} className={`r num ${tone(p.periods_qqq[k])}`}>{sp(p.periods_qqq[k])}</td>)}</tr>
                    <tr><td className="muted">行业 ETF</td><td colSpan={PERIOD_KEYS.length} className="r muted">数据接入中</td></tr>
                  </tbody>
                </table>
              ) : (
                <EmptyState />
              )}
            </Panel>
            <Panel
              title="日线"
              right={
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  {res.data && !res.data.adjusted && <span className="pill hold">未复权</span>}
                  <span className="sm"><span style={{ color: "#f5a623" }}>MA10</span> <span style={{ color: "#22d3ee" }}>MA20</span> <span style={{ color: "#a855f7" }}>MA120</span></span>
                  {YEARS.map((y) => (
                    <button key={y} type="button" className="btn2" aria-pressed={y === years} onClick={() => setYears(y)}>{y}年</button>
                  ))}
                  <label className="sm"><input type="checkbox" checked={adjust} onChange={(e) => setAdjust(e.target.checked)} /> 含分红复权</label>
                </span>
              }
            >
              <ApiState result={res} emptyWhen={(d) => d.bars.length === 0}>
                {() => <CandleChart height={420} bars={candles} mas={MAS} periods={periods} trades={trades} earnings={earningsDates} focus={focus} />}
              </ApiState>
            </Panel>
            <Panel
              grow
              title={tab === "strategy" ? "我的策略" : tab === "news" ? "新闻" : tab === "earnings" ? "财报" : "回测中的表现"}
              right={<Tabs tabs={[{ key: "strategy", label: "我的策略" }, { key: "news", label: "新闻" }, { key: "earnings", label: "财报" }, { key: "backtest", label: "回测中的表现" }]} value={tab} onChange={setTab} />}
            >
              {tab === "strategy" && <StrategyTab p={p} account={account} focus={focus} onFocus={setFocus} />}
              {tab === "news" && <EmptyState kind="pending" />}
              {tab === "earnings" && <EarningsHistory p={p} />}
              {tab === "backtest" && <EmptyState kind="pending" note="随回测页上线（#67）" />}
            </Panel>
          </div>
        </Cell>
        <Cell span={3}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Panel title="关键数据">
              {p && q ? (
                <>
                  <RangeBar low={q.low_52w} high={q.high_52w} value={q.close} />
                  <table className="dt compact kv">
                    <tbody>
                      <tr><td className="muted">量比</td><td className="r num">{num(p.key_stats.volume_ratio)}</td></tr>
                      <tr><td className="muted">Beta（1 年，对 SPY）</td><td className="r num">{num(p.key_stats.beta_1y)}</td></tr>
                      <tr><td className="muted">20 日年化波动</td><td className="r num">{pct(p.key_stats.vol_20d, 1)}</td></tr>
                      <tr><td className="muted">距 52 周高点</td><td className="r num">{sp(p.key_stats.from_high)}</td></tr>
                      <tr><td className="muted">距 120 日线</td><td className="r num">{sp(p.key_stats.to_ma120)}</td></tr>
                      <tr><td className="muted">下次财报</td><td className="r num">{p.key_stats.next_earnings ?? "—"}</td></tr>
                      <tr><td className="muted">市值 / 板块</td><td className="r muted">数据接入中</td></tr>
                    </tbody>
                  </table>
                </>
              ) : (
                <EmptyState />
              )}
            </Panel>
            <Panel title="估值与财务"><EmptyState kind="pending" /></Panel>
            <Panel title={p?.key_stats.days_to_earnings != null ? `财报 · 还有 ${p.key_stats.days_to_earnings} 个交易日` : "财报"}>
              {p?.earnings.next || p?.earnings.history.length ? (
                <div className="sm">
                  <div>下次：<b className="num">{p.earnings.next ?? "—"}</b>{p.earnings.held_through && <span className="pill hold" style={{ marginLeft: 6 }}>财报前持有中</span>}</div>
                  <div className="muted" style={{ margin: "6px 0 4px" }}>过去 {p.earnings.history.length} 次财报当天涨跌</div>
                  <div className="chips-row">
                    {p.earnings.history.map((e) => <span key={e.date} className={`chip2 num ${tone(e.move)}`}>{sp(e.move, 1)}</span>)}
                  </div>
                </div>
              ) : (
                <EmptyState />
              )}
            </Panel>
            <Panel title="相关新闻"><EmptyState kind="pending" /></Panel>
            <Panel title="同板块" grow><EmptyState kind="pending" /></Panel>
          </div>
        </Cell>
      </Grid>
    </div>
  );
}

function StrategyTab({ p, account, focus, onFocus }: { p: StockProfile | null; account: string; focus: string | null; onFocus: (d: string) => void }) {
  if (!p) return <EmptyState />;
  const t = p.technicals;
  const ranks = p.rank_history.filter((r) => r.rank != null);
  return (
    <Grid>
      <Cell span={4}>
        <Panel title="波段信号" right={<span className="muted sm">V2 规则</span>}>
          <table className="dt compact kv">
            <tbody>
              <tr><td className="muted">站上 120 日线且向上</td><td className="r">{yesNo(p.swing.trend_up, "满足", "不满足")}</td></tr>
              <tr><td className="muted">20 日线支撑</td><td className="r">{yesNo(p.swing.above_support, "站上", "跌破")}</td></tr>
              <tr><td className="muted">距 10 日线（退出线）</td><td className={`r num ${tone(p.swing.dist_exit)}`}>{sp(p.swing.dist_exit, 1)}</td></tr>
            </tbody>
          </table>
        </Panel>
      </Cell>
      <Cell span={4}>
        <Panel title="动量排名" right={<span className="muted sm">{p.relation.universe_size ? `在候选池 ${p.relation.universe_size} 只中` : ""}</span>}>
          {ranks.length ? (
            <div className="chips-row">
              {ranks.slice(-12).map((r) => <span key={r.date} className="chip2 num" title={r.date}>{r.rank}</span>)}
            </div>
          ) : (
            <div className="muted sm">近期不在前 50 名</div>
          )}
        </Panel>
      </Cell>
      <Cell span={4}>
        <Panel title="技术面" right={<span className="muted sm">由日线计算</span>}>
          <table className="dt compact kv">
            <tbody>
              <tr><td className="muted">RSI(14)</td><td className="r num">{num(t.rsi14, 1)}</td></tr>
              <tr><td className="muted">ATR(14) / 价格</td><td className="r num">{pct(t.atr14_pct, 2)}</td></tr>
              <tr><td className="muted">相对 QQQ · 20 日</td><td className="r num">{sp(t.rs_20d, 1)}</td></tr>
              <tr><td className="muted">相对 QQQ · 60 日</td><td className="r num">{sp(t.rs_60d, 1)}</td></tr>
              <tr><td className="muted">距 MA10 / MA20</td><td className="r num">{sp(t.dist_ma10, 1)} / {sp(t.dist_ma20, 1)}</td></tr>
              <tr><td className="muted">距 MA120</td><td className="r num">{sp(t.dist_ma120, 1)}</td></tr>
            </tbody>
          </table>
        </Panel>
      </Cell>
      <Cell span={12}>
        <Panel title="交易记录" right={<span className="muted sm">{account === "live" ? "实盘" : account === "paper" ? "模拟盘" : "本地模拟"} · 点击行在图上定位</span>}>
          {p.trades.length ? (
            <div className="table-wrap" style={{ maxHeight: 220 }}>
              <table className="dt" data-testid="trades">
                <thead><tr><th>时间 (ET)</th><th>方向</th><th className="r">数量</th><th className="r">价格</th><th className="r">佣金</th></tr></thead>
                <tbody>
                  {p.trades.map((f, i) => {
                    const d = toETDate(f.filled_at);
                    return (
                      <tr key={`${f.filled_at}-${i}`} className="click" aria-selected={focus === d} onClick={() => onFocus(d)}>
                        <td>{formatDateTimeET(f.filled_at)}</td>
                        <td><span className={`pill ${f.side === "BUY" ? "ok" : "err"}`}>{f.side === "BUY" ? "买入" : "卖出"}</span></td>
                        <td className="r num">{f.qty}</td>
                        <td className="r num">{num(f.price)}</td>
                        <td className="r num">{num(f.commission)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState />
          )}
        </Panel>
      </Cell>
    </Grid>
  );
}

function EarningsHistory({ p }: { p: StockProfile | null }) {
  if (!p || p.earnings.history.length === 0) return <EmptyState />;
  return (
    <table className="dt compact">
      <thead><tr><th>反应日</th><th className="r">当天涨跌</th></tr></thead>
      <tbody>
        {[...p.earnings.history].reverse().map((e) => (
          <tr key={e.date}><td>{e.date}</td><td className={`r num ${tone(e.move)}`}>{sp(e.move, 1)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}
