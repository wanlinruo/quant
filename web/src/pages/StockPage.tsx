import { useMemo, useState } from "react";
import { useParams } from "react-router";
import type { BarsOut } from "../api/types";
import { ApiState } from "../components/ApiState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { CandleChart } from "../charts/CandleChart";
import { useApi } from "../hooks/useApi";
import { num, signedPct } from "../lib/format";

const YEARS = [1, 2, 3, 5, 10, 20];
const MAS = [10, 20, 120];

function startOf(years: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

/** 个股页：日线 K 线（复权开关：factor = adj_close / close，同原行情页）。 */
export function StockPage() {
  const symbol = (useParams().symbol ?? "").toUpperCase();
  const [years, setYears] = useState(2);
  const [adjusted, setAdjusted] = useState(false);
  const start = useMemo(() => startOf(years), [years]);
  const res = useApi<BarsOut>(`/api/market/bars/${encodeURIComponent(symbol)}`, { start });
  const list = res.data?.bars;

  const candles = useMemo(
    () =>
      (list ?? []).map((b) => {
        const f = adjusted && b.close ? b.adj_close / b.close : 1;
        return { date: b.date, open: b.open * f, high: b.high * f, low: b.low * f, close: b.close * f, volume: b.volume };
      }),
    [list, adjusted],
  );
  const last = list?.at(-1);
  const prev = list && list.length > 1 ? list.at(-2) : undefined;

  return (
    <div className="page">
      <Grid>
        <Cell span={12}>
          <section className="panel" style={{ flexDirection: "row", alignItems: "center", gap: 16 }}>
            <h1 style={{ margin: 0, fontSize: 22, color: "#fff" }}>{symbol}</h1>
            {last && (
              <>
                <span className="num" style={{ fontSize: 26, color: "#fff", fontWeight: 700 }}>{num(last.close)}</span>
                {prev && (
                  <span className={`num ${last.close >= prev.close ? "up" : "down"}`} style={{ fontSize: 15 }}>
                    {signedPct(last.close / prev.close - 1)}
                  </span>
                )}
                <span className="muted sm">{last.date} 收盘</span>
              </>
            )}
          </section>
        </Cell>
      </Grid>
      <Grid>
        <Cell span={12}>
          <Panel
            title="日线"
            right={
              <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span className="sm"><span style={{ color: "#f5a623" }}>MA10</span> <span style={{ color: "#22d3ee" }}>MA20</span> <span style={{ color: "#a855f7" }}>MA120</span></span>
                {YEARS.map((y) => (
                  <button key={y} type="button" className="btn2" aria-pressed={y === years} onClick={() => setYears(y)}>
                    {y}年
                  </button>
                ))}
                <label className="sm">
                  <input type="checkbox" checked={adjusted} onChange={(e) => setAdjusted(e.target.checked)} /> 含分红复权
                </label>
              </span>
            }
          >
            <ApiState result={res} emptyWhen={(d) => d.bars.length === 0}>{() => <CandleChart height={460} bars={candles} mas={MAS} />}</ApiState>
          </Panel>
        </Cell>
      </Grid>
    </div>
  );
}
