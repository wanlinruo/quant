import { useState } from "react";
import type { DataStatusRow, SysCardOut, SystemOverview } from "../api/types";
import { ApiState } from "../components/ApiState";
import { DataTable } from "../components/DataTable";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { Stat } from "../components/Stat";
import { useApi } from "../hooks/useApi";
import { formatDateTimeET } from "../lib/time";
import { useShell } from "../shell";
import { AttemptsDialog, JobGrid } from "./system/JobGrid";
import { Banner, Events, Pending, Queue, Risk, Sources, Timeline, Upcoming } from "./system/Panels";

const ROW4_H = 330;
const ROW5_H = 300;
const ROW6_H = 260;
const RESOURCES = ["CPU", "内存", "磁盘", "老虎接口延迟"];

function card(c: SysCardOut) {
  // 熔断开关只显示，修改只能在服务器命令行
  const sub = c.key === "kill_switch" ? `${c.sub ? `${c.sub} · ` : ""}只能在服务器命令行修改：quant trading` : c.sub;
  return <div className={`sys-card ${c.level ?? "info"}`}><Stat label={c.label} value={c.value} sub={sub || undefined} /></div>;
}

/** 系统页（只读，设计 5.5）：熔断开关只显示，不提供修改入口。 */
export function SystemPage() {
  const { account } = useShell();
  const overview = useApi<SystemOverview>("/api/system/overview", { mode: account });
  const status = useApi<DataStatusRow[]>("/api/market/data-status");
  const [pick, setPick] = useState<{ job: string; date: string } | null>(null);

  return (
    <div className="page">
      <ApiState result={overview}>
        {(o) => (
          <>
            <Banner banner={o.banner} />
            <Grid>
              {(o.cards ?? []).map((c) => <Cell key={c.key} span={2}>{card(c)}</Cell>)}
            </Grid>
            <Grid>
              {RESOURCES.map((r) => <Cell key={r} span={3}><Stat label={r} value="—" sub={<Pending />} /></Cell>)}
            </Grid>
            <Grid>
              <Cell span={9}>
                <Panel title="定时任务 · 近 30 个交易日" right={<span className="muted">点格子看运行详情</span>} height={ROW4_H} scroll>
                  {o.grid ? <JobGrid grid={o.grid} onPick={setPick} /> : <div className="muted">暂无任务记录</div>}
                </Panel>
              </Cell>
              <Cell span={3}>
                <Panel title={`今天 · ${o.today}`} height={ROW4_H} scroll>
                  <Timeline rows={o.timeline ?? []} />
                </Panel>
              </Cell>
            </Grid>
            <Grid>
              <Cell span={5}><Panel title="数据源与额度" height={ROW5_H} scroll><Sources rows={o.sources ?? []} /></Panel></Cell>
              <Cell span={4}><Panel title="风控限额" height={ROW5_H} scroll><Risk rows={o.risk ?? []} /></Panel></Cell>
              <Cell span={3}><Panel title="接下来的交易日" height={ROW5_H} scroll><Upcoming rows={o.upcoming ?? []} /></Panel></Cell>
            </Grid>
            <Grid>
              <Cell span={5}><Panel title="服务容器" height={ROW6_H}><Pending /></Panel></Cell>
              <Cell span={4}><Panel title="告警与事件 · 近 30 天" height={ROW6_H} scroll><Events rows={o.events ?? []} /></Panel></Cell>
              <Cell span={3}>
                <Panel title="回测队列" height={ROW6_H}>
                  {o.queue ? <Queue q={o.queue} /> : <div className="muted">暂无回测记录</div>}
                </Panel>
              </Cell>
            </Grid>
            <Grid>
              <Cell span={9}><Panel title="资源趋势 · 7 天" height={200}><Pending /></Panel></Cell>
              <Cell span={3}><Panel title="磁盘写满预测" height={200}><Pending /></Panel></Cell>
            </Grid>
            {pick && <AttemptsDialog mode={account} pick={pick} onClose={() => setPick(null)} />}
          </>
        )}
      </ApiState>
      <Grid>
        <Cell span={12}>
          <Panel title="行情数据状态">
            <ApiState result={status}>
              {(rows) => (
                <DataTable<DataStatusRow>
                  maxHeight={420}
                  rowKey={(r) => r.symbol}
                  rows={rows}
                  columns={[
                    { key: "symbol", label: "标的" },
                    { key: "last_date", label: "最新日期" },
                    { key: "rows", label: "行数", align: "right" },
                    { key: "sources", label: "来源" },
                    { key: "last_ingested", label: "最近写入 (ET)", render: (r) => formatDateTimeET(r.last_ingested) },
                    { key: "ok", label: "校验", render: (r) => <span className={`pill ${r.ok ? "ok" : "err"}`}>{r.ok ? "通过" : "未通过"}</span> },
                    { key: "issues", label: "问题" },
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
