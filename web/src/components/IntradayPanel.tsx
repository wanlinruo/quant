import { useEffect } from "react";
import { Link } from "react-router";
import type { HoldingRow, IntradayOut, IntradayQuoteOut } from "../api/types";
import { useApi } from "../hooks/useApi";
import { num, signedMoney, signedPct } from "../lib/format";
import { etParts } from "../lib/time";
import { Panel } from "./Panel";

const INTRADAY_REFRESH_MS = import.meta.env.MODE === "test" ? 200 : 60_000;
const INDEX = ["SPY", "QQQ", "DIA", "IWM"];
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");

/** 首页盘中行情（#93）：只供查看，交易用收盘日线。接口出错时整块不显示。 */
export function IntradayPanel({ holdings, link }: { holdings: HoldingRow[]; link: (s: string) => { pathname: string; search: string } }) {
  const res = useApi<IntradayOut>("/api/market/intraday");
  const { reload } = res;
  useEffect(() => {
    const t = setInterval(reload, INTRADAY_REFRESH_MS);
    return () => clearInterval(t);
  }, [reload]);
  const d = res.data;
  if (!d) return null;
  const head = (
    <span className="sm">
      <span className={`pill ${d.session === "regular" ? (d.stale ? "warn" : "ok") : "hold"}`}>{d.session_text}</span>{" "}
      <span className="muted">{d.note}{d.as_of ? ` · 截至 ${etParts(d.as_of).time} ET` : ""}</span>
    </span>
  );
  const quotes = new Map((d.quotes ?? []).map((q) => [q.symbol, q]));
  if (d.session !== "regular" || quotes.size === 0)
    return <section className="panel intraday-line" data-testid="intraday"><b>盘中行情</b> {head}</section>;
  const rows = holdings
    .map((h) => ({ h, q: quotes.get(h.symbol) }))
    .filter((x): x is { h: HoldingRow; q: IntradayQuoteOut } => x.q !== undefined);
  const pnl = (h: HoldingRow, q: IntradayQuoteOut) => (q.prev_close == null ? null : h.qty * (q.price - q.prev_close));
  const total = rows.reduce((a, { h, q }) => a + (pnl(h, q) ?? 0), 0);
  return (
    <div data-testid="intraday">
      <Panel title="盘中行情" right={head}>
        <div className="intraday-grid">
          <div className="intraday-index">
            {INDEX.map((s) => {
              const q = quotes.get(s);
              return (
                <Link key={s} to={link(s)} className="intraday-cell">
                  <span className="muted sm">{s}</span>
                  <b className="num">{q ? num(q.price, 2) : "—"}</b>
                  <span className={`num sm ${tone(q?.change_pct)}`}>{q ? signedPct(q.change_pct) : ""}</span>
                </Link>
              );
            })}
          </div>
          {rows.length > 0 && (
            <table className="dt compact">
              <thead><tr><th>持仓</th><th className="r">盘中价</th><th className="r">今日涨跌</th><th className="r">股数</th><th className="r">当日盈亏</th></tr></thead>
              <tbody>
                {rows.map(({ h, q }) => (
                  <tr key={h.symbol}>
                    <td><Link to={link(h.symbol)}>{h.symbol}</Link></td>
                    <td className="r num">{num(q.price, 2)}</td>
                    <td className={`r num ${tone(q.change_pct)}`}>{signedPct(q.change_pct)}</td>
                    <td className="r num">{h.qty}</td>
                    <td className={`r num ${tone(pnl(h, q))}`}>{signedMoney(pnl(h, q))}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr><td colSpan={4} className="muted">合计（有盘中报价的持仓）</td><td className={`r num ${tone(total)}`} data-testid="intraday-total">{signedMoney(total)}</td></tr></tfoot>
            </table>
          )}
        </div>
      </Panel>
    </div>
  );
}
