import type { AccountPoint, FillOut, OrderOut, Position, SignalOut } from "../api/types";
import { ApiState } from "../components/ApiState";
import { DataTable } from "../components/DataTable";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { Stat } from "../components/Stat";
import { LineChart } from "../charts/LineChart";
import { useApi } from "../hooks/useApi";
import { money, num, pct, signedPct } from "../lib/format";
import { formatDateTimeET } from "../lib/time";
import { useShell } from "../shell";
import { weightRows, type WeightRow } from "../lib/weights";

const LIMIT_H = 360;

export function PortfolioPage() {
  const { account } = useShell();
  const live = account === "live";
  const base = `/api/portfolio/${account}`;
  const curve = useApi<AccountPoint[]>(`${base}/curve`);
  const positions = useApi<Position[]>(`${base}/positions`);
  const orders = useApi<OrderOut[]>(`${base}/orders`);
  const fills = useApi<FillOut[]>(`${base}/fills`);
  const signals = useApi<SignalOut[]>("/api/signals/latest", { mode: account });

  const last = curve.data?.at(-1);
  const signal = signals.data?.[0];

  return (
    <div className="page">
      <Grid>
        <Cell span={3}><Stat label={live ? "账户净值 · 实盘" : "账户净值"} value={last ? money(last.net_liquidation) : "-"} /></Cell>
        <Cell span={3}><Stat label="现金" value={last ? money(last.cash) : "-"} /></Cell>
        <Cell span={3}><Stat label="快照日" value={last?.date ?? "-"} /></Cell>
        <Cell span={3}><Stat label="持仓数" value={positions.data ? positions.data.length : "-"} /></Cell>
      </Grid>
      <Grid>
        <Cell span={12}>
          <Panel title="账户净值" live={live}>
            <ApiState result={curve}>
              {(pts) => (
                <LineChart
                  height={260}
                  series={[{ name: "净值", color: "#2962ff", points: pts.map((p) => ({ time: p.date, value: p.net_liquidation })) }]}
                />
              )}
            </ApiState>
          </Panel>
        </Cell>
      </Grid>
      <Grid>
        <Cell span={8}>
          <Panel title="持仓与目标" live={live} scroll height={LIMIT_H}>
            <ApiState result={positions} emptyWhen={(p) => p.length === 0 && !signal}>
              {(pos) => (
                <DataTable<WeightRow>
                  rowKey={(r) => r.symbol}
                  rows={weightRows(pos, signal)}
                  columns={[
                    { key: "symbol", label: "标的" },
                    { key: "target", label: "目标权重", align: "right", render: (r) => pct(r.target, 1) },
                    { key: "actual", label: "实际权重", align: "right", render: (r) => pct(r.actual, 1) },
                    {
                      key: "diff", label: "差异", align: "right",
                      render: (r) => <span className={r.diff > 0 ? "up" : r.diff < 0 ? "down" : ""}>{signedPct(r.diff, 1)}</span>,
                    },
                  ]}
                />
              )}
            </ApiState>
          </Panel>
        </Cell>
        <Cell span={4}>
          <Panel title="最新信号" height={LIMIT_H}>
            <ApiState result={signals}>
              {(sigs) => (
                <div>
                  {sigs.map((s) => (
                    <div key={s.strategy} style={{ marginBottom: 8 }}>
                      <b>{s.strategy}</b>
                      <div className="muted sm">信号日 {s.signal_date} · 数据截至 {s.data_as_of}</div>
                      <div className="sm">{Object.keys(s.target_weights).length} 个标的</div>
                    </div>
                  ))}
                </div>
              )}
            </ApiState>
          </Panel>
        </Cell>
      </Grid>
      <Grid>
        <Cell span={6}>
          <Panel title="订单" live={live} height={LIMIT_H}>
            <ApiState result={orders}>
              {(rows) => (
                <DataTable<OrderOut>
                  maxHeight={LIMIT_H - 50}
                  rowKey={(r) => r.client_order_id}
                  rows={rows}
                  columns={[
                    { key: "created_at", label: "时间 (ET)", render: (r) => formatDateTimeET(r.created_at) },
                    { key: "symbol", label: "标的" },
                    { key: "side", label: "方向", render: (r) => <span className={`pill ${r.side === "BUY" ? "ok" : "err"}`}>{r.side === "BUY" ? "买入" : "卖出"}</span> },
                    { key: "qty", label: "数量", align: "right" },
                    { key: "limit_price", label: "限价", align: "right", render: (r) => num(r.limit_price) },
                    { key: "status", label: "状态" },
                    { key: "avg_fill_price", label: "成交均价", align: "right", render: (r) => num(r.avg_fill_price) },
                    { key: "reason", label: "原因" },
                  ]}
                />
              )}
            </ApiState>
          </Panel>
        </Cell>
        <Cell span={6}>
          <Panel title="成交" live={live} height={LIMIT_H}>
            <ApiState result={fills}>
              {(rows) => (
                <DataTable<FillOut>
                  maxHeight={LIMIT_H - 50}
                  rowKey={(r, i) => `${r.filled_at}-${r.symbol}-${i}`}
                  rows={rows}
                  columns={[
                    { key: "filled_at", label: "时间 (ET)", render: (r) => formatDateTimeET(r.filled_at) },
                    { key: "symbol", label: "标的" },
                    { key: "side", label: "方向" },
                    { key: "qty", label: "数量", align: "right" },
                    { key: "price", label: "价格", align: "right", render: (r) => num(r.price) },
                    { key: "commission", label: "佣金", align: "right", render: (r) => num(r.commission) },
                  ]}
                />
              )}
            </ApiState>
          </Panel>
        </Cell>
      </Grid>
    </div>
  );
}
