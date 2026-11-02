import { useMemo, useState } from "react";
import { downloadCsv } from "../../lib/csv";
import type { BacktestTrades, BtTradeOut } from "../../api/types";
import { ApiState } from "../../components/ApiState";
import { EmptyState } from "../../components/EmptyState";
import { Cell, Grid } from "../../components/Grid";
import { Tabs } from "../../components/Tabs";
import { useApi } from "../../hooks/useApi";
import { SLEEVE_TEXT } from "../../lib/backtestStatus";
import { num, pct, signedPct } from "../../lib/format";

type PnlFilter = "all" | "win" | "loss";

function Scatter({ rows }: { rows: BtTradeOut[] }) {
  const W = 320;
  const H = 180;
  const maxDays = Math.max(...rows.map((t) => t.days), 1);
  const rets = rows.map((t) => t.ret ?? 0);
  const lim = Math.max(...rets.map(Math.abs), 0.01);
  const y = (r: number) => H / 2 - (r / lim) * (H / 2 - 6);
  return (
    <svg data-testid="scatter" viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="持有天数 × 收益">
      <line x1={0} x2={W} y1={H / 2} y2={H / 2} stroke="#2a2f3b" />
      {rows.map((t) => (
        <circle key={t.seq} cx={6 + (t.days / maxDays) * (W - 12)} cy={y(t.ret ?? 0)} r={3} fill={(t.ret ?? 0) >= 0 ? "#22c55e" : "#ef4444"} opacity={0.75}>
          <title>{`${t.symbol} ${t.days} 天 ${signedPct(t.ret, 1)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function exportCsv(runId: number, rows: BtTradeOut[]) {
  const head = ["seq", "symbol", "sleeve", "open_date", "open_price", "close_date", "close_price", "qty", "days", "ret", "pnl", "exit_reason"];
  downloadCsv(`backtest-${runId}-trades.csv`, [head, ...rows.map((t) => [t.seq, t.symbol, t.sleeve, t.open_date, t.open_price, t.close_date, t.close_price, t.qty, t.days, t.ret, t.pnl, t.exit_reason])]);
}

/** 交易明细：汇总卡、逐笔表（筛选、导出、点一行在主图标出开平仓）、散点、按平仓原因、按股票 */
export function TradesTab({ runId, selected, onSelect }: { runId: number; selected: number | null; onSelect: (t: BtTradeOut) => void }) {
  const res = useApi<BacktestTrades>(`/api/backtests/${runId}/trades`);
  const [sleeve, setSleeve] = useState("all");
  const [pnl, setPnl] = useState<PnlFilter>("all");
  const all = useMemo(() => res.data?.trades ?? [], [res.data]);
  const sleeves = useMemo(() => [...new Set(all.map((t) => t.sleeve))], [all]);
  const rows = all.filter((t) => (sleeve === "all" || t.sleeve === sleeve) && (pnl === "all" || (pnl === "win" ? t.pnl > 0 : t.pnl < 0)));
  return (
    <div data-testid="tab-交易明细">
      <ApiState result={res}>
        {(d) => {
          if (d.note || !d.summary) return <EmptyState note={d.note ?? undefined} />;
          const s = d.summary;
          const cards: [string, string][] = [
            ["交易笔数", `${s.trades}（已平仓 ${s.closed}）`],
            ["胜率", s.win_rate == null ? "—" : pct(s.win_rate, 1)],
            ["盈亏比", s.profit_factor == null ? "—" : num(s.profit_factor, 2)],
            ["平均持有", s.avg_days == null ? "—" : `${num(s.avg_days, 1)} 天`],
            ["平均收益", s.avg_ret == null ? "—" : signedPct(s.avg_ret, 1)],
            ["最好 / 最差", `${s.best?.symbol ?? "—"} ${s.best ? signedPct(s.best.ret, 1) : ""} / ${s.worst?.symbol ?? "—"} ${s.worst ? signedPct(s.worst.ret, 1) : ""}`],
          ];
          return (
            <>
              <div className="cards6">
                {cards.map(([k, v]) => (
                  <div key={k} className="stat"><div className="muted sm">{k}</div><div className="num stat-v">{v}</div></div>
                ))}
              </div>
              <Grid>
                <Cell span={8}>
                  <div className="subpanel">
                    <div className="subpanel-h">
                      <span style={{ display: "flex", gap: 8 }}>
                        <Tabs tabs={[{ key: "all", label: "全部" }, ...sleeves.map((x) => ({ key: x, label: SLEEVE_TEXT[x] ?? x }))]} value={sleeve} onChange={setSleeve} />
                        <Tabs tabs={[{ key: "all", label: "全部" }, { key: "win", label: "盈利" }, { key: "loss", label: "亏损" }]} value={pnl} onChange={setPnl} />
                      </span>
                      <button type="button" className="btn2" onClick={() => exportCsv(runId, rows)}>导出 CSV</button>
                    </div>
                    <div className="table-wrap" style={{ maxHeight: 520 }}>
                      <table className="dt compact" data-testid="trade-table">
                        <thead><tr><th>代码</th><th>子仓</th><th>开仓</th><th className="r">价格</th><th>平仓</th><th className="r">价格</th><th className="r">天数</th><th className="r">收益</th><th className="r">盈亏</th><th>平仓原因</th></tr></thead>
                        <tbody>
                          {rows.flatMap((t) => [
                            <tr key={t.seq} aria-selected={selected === t.seq} className={selected === t.seq ? "sel" : undefined} onClick={() => onSelect(t)} title={t.adjustments.length ? `调整 ${t.adjustments.length} 次` : undefined}>
                              <td><b>{t.symbol}</b></td>
                              <td className="muted">{SLEEVE_TEXT[t.sleeve] ?? t.sleeve}</td>
                              <td>{t.open_date}</td><td className="r num">{num(t.open_price)}</td>
                              <td>{t.close_date ?? <span className="pill hold">持有中</span>}</td>
                              <td className="r num">{t.close_price == null ? "—" : num(t.close_price)}</td>
                              <td className="r num">{t.days}</td>
                              <td className={`r num ${(t.ret ?? 0) >= 0 ? "up" : "down"}`}>{signedPct(t.ret, 1)}</td>
                              <td className={`r num ${t.pnl >= 0 ? "up" : "down"}`}>{num(t.pnl, 0)}</td>
                              <td className="muted">{t.exit_reason ?? "—"}</td>
                            </tr>,
                            ...(selected === t.seq
                              ? [
                                  <tr key={`${t.seq}-adj`} className="adj">
                                    <td colSpan={10}>
                                      {t.adjustments.length ? (
                                        <span className="adj-list">
                                          <span className="muted">加减仓：</span>
                                          {t.adjustments.map((x) => (
                                            <span key={String(x.date)}>
                                              {String(x.date)} <b className={Number(x.qty) >= 0 ? "up" : "down"}>{Number(x.qty) >= 0 ? "+" : ""}{num(Number(x.qty), 0)} 股</b>
                                            </span>
                                          ))}
                                        </span>
                                      ) : <span className="muted">这笔交易没有中途加减仓</span>}
                                    </td>
                                  </tr>,
                                ]
                              : []),
                          ])}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </Cell>
                <Cell span={4}>
                  <div className="vstack">
                  <div className="subpanel"><div className="subpanel-h"><b>持有天数 × 收益</b></div><Scatter rows={rows} /></div>
                  <div className="subpanel">
                    <div className="subpanel-h"><b>按平仓原因</b></div>
                    <table className="dt compact">
                      <thead><tr><th>原因</th><th className="r">笔数</th><th className="r">胜率</th><th className="r">盈亏</th></tr></thead>
                      <tbody>{(d.by_reason ?? []).map((r) => <tr key={r.reason}><td>{r.reason}</td><td className="r num">{r.count}</td><td className="r num">{r.win_rate == null ? "—" : pct(r.win_rate, 0)}</td><td className={`r num ${r.pnl >= 0 ? "up" : "down"}`}>{num(r.pnl, 0)}</td></tr>)}</tbody>
                    </table>
                  </div>
                  <div className="subpanel">
                    <div className="subpanel-h"><b>按股票</b><span className="muted sm">盈亏前 10</span></div>
                    <table className="dt compact">
                      <thead><tr><th>代码</th><th className="r">笔数</th><th className="r">盈亏</th><th className="r">天数</th></tr></thead>
                      <tbody>{(d.by_symbol ?? []).slice(0, 10).map((r) => <tr key={r.symbol}><td>{r.symbol}</td><td className="r num">{r.trades}</td><td className={`r num ${r.pnl >= 0 ? "up" : "down"}`}>{num(r.pnl, 0)}</td><td className="r num">{r.days}</td></tr>)}</tbody>
                    </table>
                  </div>
                  </div>
                </Cell>
              </Grid>
            </>
          );
        }}
      </ApiState>
    </div>
  );
}
