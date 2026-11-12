import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import type { BacktestAnalysis, BacktestDataStatus, BacktestDefaults, BacktestRunOut, BtTradeOut } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { Tabs } from "../components/Tabs";
import { useApi } from "../hooks/useApi";
import { downloadCsv } from "../lib/csv";
import { OverviewTab, ParamsTab, ReturnsTab, RiskTab } from "./backtest/AnalysisTabs";
import { CompareView } from "./backtest/CompareView";
import { MainChart } from "./backtest/MainChart";
import { PositionsTab } from "./backtest/PositionsTab";
import { TradesTab } from "./backtest/TradesTab";
import { RunHeader } from "./backtest/RunHeader";
import { RobustTab } from "./backtest/RobustTab";
import { RUNS_REFRESH_MS } from "./backtest/robustRefresh";
import { RunList } from "./backtest/RunList";
import { BacktestSubmitDrawer, type DrawerInitial } from "./BacktestSubmitDrawer";

const MAX_COMPARE = 5;


const TABS = [
  { key: "overview", label: "概览" },
  { key: "returns", label: "收益分析" },
  { key: "risk", label: "回撤与风险" },
  { key: "trades", label: "交易明细" },
  { key: "positions", label: "持仓变化" },
  { key: "robust", label: "稳健性" },
  { key: "params", label: "参数与日志" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

function exportCsv(a: BacktestAnalysis) {
  const s = a.series;
  if (!s) return;
  downloadCsv(`backtest-${a.id}.csv`, [["date", "strategy", "qqq", "spy", "drawdown"], ...s.dates.map((d, i) => [d, s.strategy[i], s.qqq?.[i], s.spy?.[i], s.drawdown[i]])]);
}

export function BacktestPage() {
  const runs = useApi<BacktestRunOut[]>("/api/backtests");
  const defaults = useApi<BacktestDefaults>("/api/backtests/defaults");
  const dataStatus = useApi<BacktestDataStatus>("/api/backtests/data-status");
  const [checked, setChecked] = useState<number[] | null>(null);
  const [drawer, setDrawer] = useState<DrawerInitial | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { search: initialSearch } = useLocation();
  // 网址可指定打开的标签（?tab=trades），便于分享与截图
  const [tab, setTab] = useState<TabKey>(() => {
    const t = new URLSearchParams(initialSearch).get("tab");
    return TABS.some((x) => x.key === t) ? (t as TabKey) : "overview";
  });
  // 选中的交易只属于当时查看的回测：切换回测后不再显示（避免旧标记画在新回测上）
  const [picked, setPicked] = useState<{ run: number; t: BtTradeOut } | null>(null);

  const { id: routeId } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();

  const parentList = runs.data ?? [];
  const listedRoute = parentList.find((r) => String(r.id) === routeId);
  const directRun = useApi<BacktestRunOut>(routeId && runs.data && !listedRoute ? `/api/backtests/${routeId}` : null);
  const list = directRun.data ? [directRun.data, ...parentList] : parentList;
  const firstDone = list.find((r) => r.status === "done") ?? list[0];
  const defaultId = (listedRoute ?? directRun.data)?.id ?? firstDone?.id;
  const selected = useMemo(() => checked ?? (defaultId !== undefined ? [defaultId] : []), [checked, defaultId]);
  const current = list.find((r) => r.id === selected[0]);
  const compare = selected.length >= 2;
  const analysis = useApi<BacktestAnalysis>(current && !compare ? `/api/backtests/${current.id}/analysis` : null);
  const a = analysis.data && analysis.data.id === current?.id ? analysis.data : null;
  // 有排队或运行中的回测时定时刷新列表；选中回测的状态变化后重新取分析
  const busy = list.some((r) => r.status === "queued" || r.status === "running");
  const { reload: reloadRuns } = runs;
  const { reload: reloadAnalysis } = analysis;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(reloadRuns, RUNS_REFRESH_MS);
    return () => clearInterval(t);
  }, [busy, reloadRuns]);
  const currentStatus = current?.status;
  const seenStatus = useRef(currentStatus);
  useEffect(() => {
    if (seenStatus.current !== currentStatus && currentStatus !== undefined) reloadAnalysis();
    seenStatus.current = currentStatus;
  }, [currentStatus, reloadAnalysis]);
  const trade = picked && picked.run === current?.id ? picked.t : null;

  /** 点行：单个查看时切换到这一个；对比模式下排到第一位（满 5 个时挤掉最后一个）。同步网址。 */
  function open(id: number) {
    setChecked(compare ? [id, ...selected.filter((x) => x !== id)].slice(0, MAX_COMPARE) : [id]);
    navigate({ pathname: `/backtest/${id}`, search });
  }

  function toggle(id: number) {
    setChecked(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  return (
    <div className="page">
      {notice && <div className="pill ok" style={{ alignSelf: "flex-start", padding: "4px 10px" }}>{notice}</div>}
      {dataStatus.data && <DataStatusBar s={dataStatus.data} />}
      <Grid>
        <Cell span={3}>
          <RunList
                result={runs}
                selected={selected}
                currentId={current?.id}
                max={MAX_COMPARE}
                canCreate={!!defaults.data}
                onOpen={open}
                onToggle={toggle}
                onCreate={() => setDrawer("new")}
              />
        </Cell>
        <Cell span={9}>
          {current && compare ? (
            <CompareView ids={selected} />
          ) : current ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <RunHeader
                run={current}
                a={a}
                warnings={<CoverageWarnings run={current} />}
                onParams={() => setTab("params")}
                onCopy={() => setDrawer({ strategy: current.strategy, start: current.start, end: current.end, params: current.params, costs: current.costs, cash: current.initial_cash != null ? String(current.initial_cash) : a?.initial_cash != null ? String(a.initial_cash) : null })}
                onExport={() => a && exportCsv(a)}
              />
              <MainChart
                s={a?.series}
                strategy={current.strategy}
                oosStart={a?.oos_start ?? "2023-01-01"}
                marks={trade ? [trade.open_date, ...(trade.close_date ? [trade.close_date] : [])] : undefined}
              />
              <Panel title={TABS.find((t) => t.key === tab)?.label ?? ""} right={<Tabs tabs={TABS.map((t) => ({ key: t.key, label: t.label }))} value={tab} onChange={setTab} />}>
                <TabBody tab={tab} a={a} loading={analysis.loading} error={analysis.error?.message ?? null} runId={current.id} trade={trade?.seq ?? null} onTrade={(t) => setPicked({ run: current.id, t })} />
              </Panel>
            </div>
          ) : (
            <Panel title="回测报告"><EmptyState note={list.length ? "勾选回测查看（左侧列表）" : "还没有回测记录，点击“+ 新建回测”提交"} /></Panel>
          )}
        </Cell>
      </Grid>
      {drawer && defaults.data && (
        <BacktestSubmitDrawer
          defaults={defaults.data}
          initial={drawer === "new" ? undefined : drawer}
          onClose={() => setDrawer(null)}
          onSubmitted={(id) => {
            setDrawer(null);
            setNotice(`已提交回测 #${id}，backtest-worker 执行完成后显示在列表中`);
            runs.reload();
          }}
        />
      )}
    </div>
  );
}

type TabBodyProps = { tab: TabKey; a: BacktestAnalysis | null; loading: boolean; error: string | null; runId: number; trade: number | null; onTrade: (t: BtTradeOut) => void };

function TabBody({ tab, a, loading, error, runId, trade, onTrade }: TabBodyProps) {
  if (tab === "trades") return <TradesTab runId={runId} selected={trade} onSelect={onTrade} />;
  if (tab === "positions") return <PositionsTab runId={runId} />;
  if (tab === "robust") return <RobustTab runId={runId} strategy={a?.strategy ?? ""} />;
  if (!a) return error ? <div className="error-box">分析加载失败：{error}</div> : loading ? <div className="muted sm">加载中…</div> : <EmptyState />;
  if (tab === "params") return <ParamsTab a={a} />;
  if (a.status !== "done") return <EmptyState note={a.status === "failed" ? `回测失败：${a.error ?? ""}` : "回测尚未完成"} />;
  if (tab === "overview") return <OverviewTab a={a} />;
  if (tab === "returns") return <ReturnsTab a={a} />;
  return <RiskTab a={a} />;
}

/** 服务器回测数据（Tiingo）的补齐进度；Mac 上的 yfinance 模式不显示。 */
function DataStatusBar({ s }: { s: BacktestDataStatus }) {
  if (s.source !== "tiingo" || !s.fixed || !s.current || !s.delisted || !s.quota) return null;
  const pending = s.fixed.done < s.fixed.total || s.current.done < s.current.total;
  return (
    <div className="panel sm" style={{ flexDirection: "row", gap: 12, alignItems: "center", whiteSpace: "nowrap", overflow: "hidden" }}>
      <span>回测数据：Tiingo · 截至 {s.data_end ?? "—"}</span>
      <span className="muted">固定标的 {s.fixed.done}/{s.fixed.total} · 当前成分 {s.current.done}/{s.current.total} · 历史成分 {s.delisted.done}/{s.delisted.total}</span>
      <span className="muted">本月额度 {s.quota.month_symbols}/{s.quota.month_limit}</span>
      {pending && <span className="pill warn">补齐中</span>}
      {s.delisted.done < s.delisted.total && <span className="pill warn">缺已退市成分：长周期结果偏乐观</span>}
      {s.earnings !== "complete" && <span className="pill hold">历史财报日同步中</span>}
    </div>
  );
}

type DataInfo = { coverage?: number; earnings?: string };

function CoverageWarnings({ run }: { run: BacktestRunOut }) {
  const d = run.metrics.data as DataInfo | undefined;
  if (!d) return null;
  return (
    <>
      {d.coverage !== undefined && d.coverage < 0.999 && <span className="pill warn">数据覆盖 {Math.round(d.coverage * 100)}%</span>}
      {d.earnings !== undefined && d.earnings !== "complete" && <span className="pill warn">历史财报日缺失</span>}
    </>
  );
}
