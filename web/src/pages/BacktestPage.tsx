import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import type { BacktestAnalysis, BacktestDataStatus, BacktestDefaults, BacktestRunOut, BtTradeOut, EquityPoint } from "../api/types";
import { ApiState } from "../components/ApiState";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { Cell, Grid } from "../components/Grid";
import { Panel } from "../components/Panel";
import { Tabs } from "../components/Tabs";
import { LineChart } from "../charts/LineChart";
import { useApi } from "../hooks/useApi";
import { fmtMetric, METRICS } from "../lib/metrics";
import { OverviewTab, ParamsTab, ReturnsTab, RiskTab } from "./backtest/AnalysisTabs";
import { MainChart } from "./backtest/MainChart";
import { PositionsTab } from "./backtest/PositionsTab";
import { TradesTab } from "./backtest/TradesTab";
import { RunHeader } from "./backtest/RunHeader";
import { RunList } from "./backtest/RunList";
import { BacktestSubmitDrawer, type DrawerInitial } from "./BacktestSubmitDrawer";

const MAX_COMPARE = 5;
const COLORS = ["#2962ff", "#a855f7", "#22c55e", "#f5a623", "#22d3ee"];


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
  const rows = ["date,strategy,qqq,spy,drawdown", ...s.dates.map((d, i) => [d, s.strategy[i], s.qqq?.[i] ?? "", s.spy?.[i] ?? "", s.drawdown[i]].join(","))];
  const url = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `backtest-${a.id}.csv`;
  link.click();
  URL.revokeObjectURL(url);
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

  const list = runs.data ?? [];
  const firstDone = list.find((r) => r.status === "done") ?? list[0];
  const defaultId = list.find((r) => String(r.id) === routeId)?.id ?? firstDone?.id;
  const selected = useMemo(() => checked ?? (defaultId !== undefined ? [defaultId] : []), [checked, defaultId]);
  const current = list.find((r) => r.id === selected[0]);
  const compare = selected.length >= 2;
  const equity = useApi<EquityPoint[]>(compare ? "/api/backtests/equity" : null, { ids: selected });
  const analysis = useApi<BacktestAnalysis>(current && !compare ? `/api/backtests/${current.id}/analysis` : null);
  const a = analysis.data && analysis.data.id === current?.id ? analysis.data : null;
  const trade = picked && picked.run === current?.id ? picked.t : null;

  const series = useMemo(() => {
    const pts = equity.data ?? [];
    return selected.map((id, i) => {
      const own = pts.filter((p) => p.run_id === id);
      const base = own[0]?.equity || 1;
      return { name: `#${id}`, color: COLORS[i % COLORS.length], points: own.map((p) => ({ time: p.date, value: p.equity / base })) };
    });
  }, [equity.data, selected]);
  const ddSeries = useMemo(
    () => selected.map((id, i) => ({
      name: `#${id}`, color: COLORS[i % COLORS.length],
      points: (equity.data ?? []).filter((p) => p.run_id === id).map((p) => ({ time: p.date, value: p.drawdown })),
    })),
    [equity.data, selected],
  );

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
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Panel title="净值对比（起点 = 1）">
                <ApiState result={equity}>{() => <LineChart height={300} series={series} />}</ApiState>
              </Panel>
              <Panel title="回撤">
                <ApiState result={equity}>{() => <LineChart height={160} series={ddSeries} percent />}</ApiState>
              </Panel>
              <Panel title="指标对比">
                <DataTable<BacktestRunOut>
                  rowKey={(r) => r.id}
                  rows={list.filter((r) => selected.includes(r.id))}
                  columns={[
                    { key: "id", label: "回测", render: (r) => `#${r.id} ${r.strategy} ${r.start}~${r.end}` },
                    ...METRICS.map((m) => ({ key: m.key, label: m.label, align: "right" as const, render: (r: BacktestRunOut) => fmtMetric(r.metrics[m.key], m.kind) })),
                  ]}
                />
              </Panel>
            </div>
          ) : current ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <RunHeader
                run={current}
                a={a}
                warnings={<CoverageWarnings run={current} />}
                onParams={() => setTab("params")}
                onCopy={() => setDrawer({ strategy: current.strategy, start: current.start, end: current.end, params: current.params, costs: current.costs, cash: a?.initial_cash != null ? String(a.initial_cash) : null })}
                onExport={() => a && exportCsv(a)}
              />
              <MainChart
                s={a?.series}
                strategy={current.strategy}
                oosStart={a?.oos_start ?? "2023-01-01"}
                marks={trade ? [trade.open_date, ...(trade.close_date ? [trade.close_date] : [])] : undefined}
              />
              <Panel title={TABS.find((t) => t.key === tab)?.label ?? ""} right={<Tabs tabs={TABS.map((t) => ({ key: t.key, label: t.label }))} value={tab} onChange={setTab} />}>
                <TabBody tab={tab} a={a} loading={analysis.loading} runId={current.id} trade={trade?.seq ?? null} onTrade={(t) => setPicked({ run: current.id, t })} />
              </Panel>
            </div>
          ) : (
            <Panel title="回测报告"><EmptyState note="还没有回测记录，点击“+ 新建回测”提交" /></Panel>
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

type TabBodyProps = { tab: TabKey; a: BacktestAnalysis | null; loading: boolean; runId: number; trade: number | null; onTrade: (t: BtTradeOut) => void };

function TabBody({ tab, a, loading, runId, trade, onTrade }: TabBodyProps) {
  if (tab === "trades") return <TradesTab runId={runId} selected={trade} onSelect={onTrade} />;
  if (tab === "positions") return <PositionsTab runId={runId} />;
  if (tab === "robust") return <EmptyState kind="pending" note="待稳健性检验（#68）：参数敏感性、walk-forward、成本与起点敏感性" />;
  if (!a) return loading ? <div className="muted sm">加载中…</div> : <EmptyState />;
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
