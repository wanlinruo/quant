import { useState } from "react";
import type { BacktestRunOut } from "../../api/types";
import { ApiState } from "../../components/ApiState";
import { Panel } from "../../components/Panel";
import type { ApiResult } from "../../hooks/useApi";
import { Tabs } from "../../components/Tabs";
import { STATUS_PILL, STATUS_TEXT } from "../../lib/backtestStatus";
import { pct } from "../../lib/format";

const FILTERS = [
  { key: "all", label: "全部" },
  { key: "done", label: "完成" },
  { key: "active", label: "进行中" },
  { key: "failed", label: "失败" },
] as const;
type Filter = (typeof FILTERS)[number]["key"];

function num(run: BacktestRunOut, key: string): number | undefined {
  const v = run.metrics[key];
  return typeof v === "number" ? v : undefined;
}

type Props = {
  result: ApiResult<BacktestRunOut[]>;
  selected: number[];
  currentId: number | undefined;
  max: number;
  canCreate: boolean;
  onOpen: (id: number) => void;
  onToggle: (id: number) => void;
  onCreate: () => void;
};

/** 左侧回测列表：状态、编号、策略、年化、回撤；运行中 / 失败原因；筛选；勾选对比。 */
export function RunList({ result, selected, currentId, max, canCreate, onOpen, onToggle, onCreate }: Props) {
  const [f, setF] = useState<Filter>("all");
  const shown = (result.data ?? []).filter((r) =>
    f === "all" ? true : f === "done" ? r.status === "done" : f === "failed" ? r.status === "failed" : r.status === "running" || r.status === "queued",
  );
  return (
    <Panel title="回测记录" right={<button type="button" className="btn" disabled={!canCreate} onClick={onCreate}>+ 新建回测</button>}>
      <Tabs tabs={FILTERS.map((x) => ({ key: x.key, label: x.label }))} value={f} onChange={setF} />
      <ApiState result={result}>{() => (
      <div className="runs" style={{ marginTop: 6 }}>
        {shown.map((r) => {
          const on = selected.includes(r.id);
          const cagr = num(r, "cagr");
          const mdd = num(r, "max_drawdown");
          return (
            <div key={r.id} className={`run${r.id === currentId ? " on" : ""}`}>
              <div className="run-main">
                <input
                  type="checkbox"
                  aria-label={`对比 #${r.id}`}
                  checked={on}
                  disabled={!on && selected.length >= max}
                  onChange={() => onToggle(r.id)}
                />
                <button type="button" className="run-open" aria-label={`打开回测 #${r.id} ${r.strategy}`} onClick={() => onOpen(r.id)}>
                  <span className={`pill ${STATUS_PILL[r.status] ?? "hold"}`}>{STATUS_TEXT[r.status] ?? r.status}</span>
                  <b>#{r.id}</b>
                  <span className="muted">{r.strategy}</span>
                  {r.robust && <span className="pill info" title="稳健性检验子回测：完成 + 失败 / 总数">{r.robust.total ? `稳健 ${r.robust.done + r.robust.failed}/${r.robust.total}` : "稳健 待生成"}</span>}
                  <span style={{ flex: 1 }} />
                  <span className="num up">{cagr !== undefined ? pct(cagr, 1) : ""}</span>
                  <span className="num down">{mdd !== undefined ? pct(mdd, 1) : ""}</span>
                </button>
              </div>
              {r.status === "failed" && r.error && <div className="run-err sm" title={r.error}>{r.error}</div>}
            </div>
          );
        })}
        {shown.length === 0 && <div className="muted sm">没有符合条件的回测</div>}
        <div className="muted sm" style={{ marginTop: 6 }}>勾选最多 {max} 个进入对比</div>
      </div>
      )}</ApiState>
    </Panel>
  );
}
