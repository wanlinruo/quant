import type { DataStatusRow, FailureOut, JobStatusOut, QueueStatus } from "../api/types";
import { ApiState } from "../components/ApiState";
import { DataTable } from "../components/DataTable";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { Stat } from "../components/Stat";
import { useApi } from "../hooks/useApi";
import { formatDateTimeET } from "../lib/time";
import { useShell } from "../shell";

const KILL_SWITCH: Record<string, string> = { true: "开启（允许下单）", false: "关闭（禁止下单）" };
const MODE_TEXT: Record<string, string> = { sim: "本地模拟 SIM", paper: "模拟盘 PAPER", live: "实盘 LIVE" };
const ROW_H = 380;

/** 系统页（只读）：熔断开关只显示，不提供修改入口。 */
export function SystemPage() {
  const { meta } = useShell();
  const flags = useApi<Record<string, string>>("/api/system/flags");
  const queue = useApi<QueueStatus>("/api/system/queue");
  const jobs = useApi<JobStatusOut[]>("/api/system/jobs");
  const failures = useApi<FailureOut[]>("/api/system/failures");
  const status = useApi<DataStatusRow[]>("/api/market/data-status");

  const flag = flags.data ? KILL_SWITCH[String(flags.data.trading_enabled)] ?? "未知" : "-";

  return (
    <div className="page">
      <Grid>
        <Cell span={3}><Stat label="交易模式" value={MODE_TEXT[meta.trading_mode]} sub={`Broker：${meta.broker}`} /></Cell>
        <Cell span={3}><Stat label="熔断开关" value={flag} sub="只能在服务器命令行修改：quant trading" /></Cell>
        <Cell span={3}><Stat label="回测运行中" value={queue.data?.running ?? "-"} sub={`完成 ${queue.data?.done ?? "-"} · 失败 ${queue.data?.failed ?? "-"}`} /></Cell>
        <Cell span={3}><Stat label="回测排队" value={queue.data?.queued ?? "-"} /></Cell>
      </Grid>
      <Grid>
        <Cell span={8}>
          <Panel title="定时任务" height={ROW_H}>
            <ApiState result={jobs}>
              {(rows) => (
                <DataTable<JobStatusOut>
                  maxHeight={ROW_H - 50}
                  rowKey={(r) => r.job_name}
                  rows={rows}
                  columns={[
                    { key: "job_name", label: "任务" },
                    { key: "trade_date", label: "交易日" },
                    { key: "status", label: "状态", render: (r) => <span className={`pill ${r.status === "success" ? "ok" : r.status === "failed" ? "err" : "info"}`}>{r.status}</span> },
                    { key: "started_at", label: "开始 (ET)", render: (r) => formatDateTimeET(r.started_at) },
                    { key: "finished_at", label: "结束 (ET)", render: (r) => formatDateTimeET(r.finished_at) },
                    { key: "error", label: "错误" },
                  ]}
                />
              )}
            </ApiState>
          </Panel>
        </Cell>
        <Cell span={4}>
          <Panel title="最近失败" height={ROW_H}>
            <ApiState result={failures}>
              {(rows) => (
                <DataTable<FailureOut>
                  maxHeight={ROW_H - 50}
                  rowKey={(r, i) => `${r.source}-${i}`}
                  rows={rows}
                  columns={[
                    { key: "time", label: "时间 (ET)", render: (r) => formatDateTimeET(r.time) },
                    { key: "source", label: "来源" },
                    { key: "message", label: "内容" },
                  ]}
                />
              )}
            </ApiState>
          </Panel>
        </Cell>
      </Grid>
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
