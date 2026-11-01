import type { AccountPoint, JobStatusOut, SignalOut } from "../api/types";
import { ApiState } from "../components/ApiState";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { LineChart } from "../charts/LineChart";
import { useApi } from "../hooks/useApi";
import { money, signedPct } from "../lib/format";
import { useShell } from "../shell";

/** 市场首页：看板待数据源接入（设计第 8 节第 5、6 步）；右侧是当前账户的“我的策略”。 */
export function MarketPage() {
  const { account } = useShell();
  const curve = useApi<AccountPoint[]>(`/api/portfolio/${account}/curve`);
  const signals = useApi<SignalOut[]>("/api/signals/latest", { mode: account });
  const jobs = useApi<JobStatusOut[]>("/api/system/jobs");

  return (
    <div className="page">
      <Grid>
        <Cell span={8}>
          <Panel title="市场看板">
            <EmptyState kind="pending" note="热力图、市场宽度、异动个股、板块表现、新闻与财经日历将在数据源接入后上线" />
          </Panel>
        </Cell>
        <Cell span={4}>
          <Panel title="我的策略" live={account === "live"}>
            <ApiState result={curve}>
              {(pts) => {
                const last = pts.at(-1)!;
                const prev = pts.length > 1 ? pts.at(-2)! : undefined;
                const chg = prev ? last.net_liquidation / prev.net_liquidation - 1 : null;
                return (
                  <div>
                    <div className="muted sm">账户净值 · {last.date}</div>
                    <div className="num" style={{ fontSize: 22, color: "#fff", fontWeight: 700 }}>
                      {money(last.net_liquidation)}{" "}
                      {chg !== null && <span className={chg >= 0 ? "up" : "down"} style={{ fontSize: 13 }}>{signedPct(chg)}</span>}
                    </div>
                    <LineChart height={90} series={[{ name: "", color: "#2962ff", points: pts.slice(-30).map((p) => ({ time: p.date, value: p.net_liquidation })) }]} />
                  </div>
                );
              }}
            </ApiState>
            <div style={{ marginTop: 10 }}>
              <ApiState result={signals}>
                {(sigs) => (
                  <div className="sm">
                    最新信号：{sigs.map((s) => `${s.strategy} · ${s.signal_date} · ${Object.keys(s.target_weights).length} 个标的`).join("；")}
                  </div>
                )}
              </ApiState>
            </div>
            <div style={{ marginTop: 6 }}>
              <ApiState result={jobs}>
                {(js) => (
                  <div className="sm">
                    最近任务：
                    {js.map((j) => (
                      <span key={j.job_name} className={`pill ${j.status === "success" ? "ok" : j.status === "failed" ? "err" : "info"}`} style={{ marginRight: 4 }}>
                        {j.job_name}
                      </span>
                    ))}
                  </div>
                )}
              </ApiState>
            </div>
          </Panel>
        </Cell>
      </Grid>
    </div>
  );
}
