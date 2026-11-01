import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { BacktestDefaults, BacktestSubmit, ParamField, SubmitResult } from "../api/types";
import { useApi } from "../hooks/useApi";

/** “复制参数新建”：用某次回测的策略、区间、资金、参数与成本预填 */
export type DrawerInitial = {
  strategy: string;
  start: string;
  end: string;
  params: Record<string, unknown>;
  costs?: Record<string, unknown> | null;
  cash?: string | null;
};

type Props = { defaults: BacktestDefaults; initial?: DrawerInitial; onClose: () => void; onSubmitted: (id: number) => void };

const str = (v: unknown, fallback: string) => (v === undefined || v === null ? fallback : String(v));
const GROUP_TEXT: Record<string, string> = { momentum: "动量仓（momentum）", swing: "波段仓（swing）" };

function fieldId(f: ParamField): string {
  return f.group ? `${f.group}.${f.key}` : f.key;
}

function toText(v: unknown): string {
  return Array.isArray(v) ? v.join(",") : v === undefined || v === null ? "" : String(v);
}

function parse(f: ParamField, text: string): unknown {
  if (f.type === "int") return Number.parseInt(text, 10);
  if (f.type === "float") return Number.parseFloat(text);
  if (f.type === "bool") return text === "true";
  if (f.type === "list") return text.split(",").map((x) => x.trim()).filter(Boolean);
  return text;
}

/** 只提交与默认值不同的参数；嵌套组只含改过的子键 */
function changedParams(fields: ParamField[], edits: Record<string, string>): Record<string, unknown> | null {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const text = edits[fieldId(f)];
    if (text === undefined || text === toText(f.default)) continue;
    const v = parse(f, text);
    if (f.group) out[f.group] = { ...((out[f.group] as Record<string, unknown>) ?? {}), [f.key]: v };
    else out[f.key] = v;
  }
  return Object.keys(out).length ? out : null;
}

function initialEdits(params: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) out[`${k}.${k2}`] = toText(v2);
    } else out[k] = toText(v);
  }
  return out;
}

const STATUS_STYLE = { position: "fixed", top: 0, right: 0, bottom: 0, width: 440 } as const;

/** 新建回测：面板唯一的写操作，只插入一条排队任务，由 backtest-worker 执行。 */
export function BacktestSubmitDrawer({ defaults, initial, onClose, onSubmitted }: Props) {
  const p = initial?.params ?? {};
  const c = initial?.costs ?? {};
  const [form, setForm] = useState({
    strategy: initial && defaults.strategies.includes(initial.strategy) ? initial.strategy : defaults.strategies.includes("etf_momentum") ? "etf_momentum" : defaults.strategies[0],
    start: initial?.start ?? defaults.start,
    end: initial?.end ?? defaults.end,
    universe: Array.isArray(p.universe) ? (p.universe as string[]) : defaults.universe,
    safe_asset: str(p.safe_asset, defaults.safe_asset),
    lookback: str(p.lookback, String(defaults.lookback)),
    top_k: str(p.top_k, String(defaults.top_k)),
    sma_window: str(p.sma_window, String(defaults.sma_window)),
    cash: str(initial?.cash, String(defaults.cash)),
    commission_per_share: str(c.commission_per_share, defaults.commission_per_share ?? ""),
    min_commission: str(c.min_commission, defaults.min_commission ?? ""),
    slippage: str(c.slippage, defaults.slippage),
  });
  const schema = useApi<Record<string, ParamField[]>>("/api/backtests/param-schema");
  // “复制参数新建”时用那次回测的参数预填（只对非表单策略）
  const [edits, setEdits] = useState<Record<string, string>>(() => (initial && initial.strategy !== "etf_momentum" ? initialEdits(p) : {}));
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function submit() {
    setBusy(true);
    setErrors([]);
    const body: BacktestSubmit = {
      strategy: form.strategy,
      start: form.start,
      end: form.end,
      universe: form.universe,
      safe_asset: form.safe_asset.trim().toUpperCase(),
      lookback: Number(form.lookback),
      top_k: Number(form.top_k),
      sma_window: Number(form.sma_window),
      cash: form.cash,
      costs: {
        commission_per_share: form.commission_per_share || null,
        min_commission: form.min_commission || null,
        slippage: form.slippage,
      },
      params: form.strategy === "etf_momentum" ? null : changedParams(schema.data?.[form.strategy] ?? [], edits),
    };
    try {
      const r = await api.post<SubmitResult>("/api/backtests", body);
      onSubmitted(r.id);
    } catch (e) {
      setErrors(e instanceof ApiError ? e.messages : [String(e)]);
    } finally {
      setBusy(false);
    }
  }

  const field = (label: string, k: keyof typeof form, type = "text", max?: string, min?: string) => (
    <label className="field">
      <span>{label}</span>
      <input type={type} value={form[k] as string} onChange={set(k)} max={max} min={min} />
    </label>
  );
  // 标的池与动量参数只属于 etf_momentum；其它策略沿用服务器配置（与模拟盘一致）
  const formStrategy = form.strategy === "etf_momentum";
  const maxEnd = defaults.data_end ?? defaults.end;

  return (
    <aside className="drawer" style={STATUS_STYLE} role="dialog" aria-label="新建回测">
      <div className="panel-h">
        <b style={{ fontSize: 15 }}>新建回测</b>
        <button type="button" className="btn2" onClick={onClose}>关闭</button>
      </div>
      <label className="field">
        <span>策略</span>
        <select value={form.strategy} onChange={set("strategy")}>
          {defaults.strategies.map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <div className="two">{field("开始", "start", "date", maxEnd, defaults.min_start ?? undefined)}{field("结束", "end", "date", maxEnd, defaults.min_start ?? undefined)}</div>
      {defaults.data_end && <div className="muted sm">回测数据截止 {defaults.data_end}</div>}
      {formStrategy ? (
        <>
      <div className="field">
        <span>标的池</span>
        <div className="chips">
          {defaults.universe_options.map((s) => (
            <label key={s} className="chip">
              <input
                type="checkbox"
                checked={form.universe.includes(s)}
                onChange={(e) => setForm({ ...form, universe: e.target.checked ? [...form.universe, s] : form.universe.filter((x) => x !== s) })}
              />{" "}
              {s}
            </label>
          ))}
        </div>
      </div>
      <div className="two">{field("避险资产", "safe_asset")}{field("动量回看（交易日）", "lookback", "number")}</div>
      <div className="two">{field("持有数量 top_k", "top_k", "number")}{field("均线窗口", "sma_window", "number")}</div>
        </>
      ) : (
        <StrategyParams fields={schema.data?.[form.strategy] ?? []} edits={edits} setEdits={setEdits} />
      )}
      {field("初始资金（美元）", "cash", "number")}
      <div className="three">{field("每股佣金", "commission_per_share")}{field("单笔最低佣金", "min_commission")}{field("滑点", "slippage")}</div>
      {errors.length > 0 && (
        <div className="error-box">{errors.map((m) => <div key={m}>{m}</div>)}</div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button type="button" className="btn" style={{ flex: 1 }} disabled={busy} onClick={submit}>提交到队列</button>
        <button type="button" className="btn2" onClick={onClose}>取消</button>
      </div>
      <div className="muted sm" style={{ marginTop: 8 }}>这是面板唯一的写操作：只插入一条排队任务，由 backtest-worker 执行。</div>
    </aside>
  );
}

function StrategyParams({ fields, edits, setEdits }: { fields: ParamField[]; edits: Record<string, string>; setEdits: (e: Record<string, string>) => void }) {
  if (!fields.length) return <div className="muted sm" style={{ margin: "6px 0" }}>加载参数中…</div>;
  const groups = [...new Set(fields.map((f) => f.group ?? ""))];
  const value = (f: ParamField) => edits[fieldId(f)] ?? toText(f.default);
  const set = (f: ParamField, text: string) => setEdits({ ...edits, [fieldId(f)]: text });
  return (
    <div className="param-form">
      <div className="muted sm" style={{ margin: "6px 0" }}>默认值与服务器配置一致（模拟盘在用的参数）；只提交改过的项，改过的标黄。</div>
      {groups.map((g) => (
        <details key={g || "top"} open={!g}>
          <summary className="sm"><b>{g ? GROUP_TEXT[g] ?? g : "组合参数"}</b></summary>
          <div className="two">
            {fields.filter((f) => (f.group ?? "") === g).map((f) => {
              const changed = value(f) !== toText(f.default);
              return (
                <label key={fieldId(f)} className={`field${changed ? " changed" : ""}`}>
                  <span>{f.key}</span>
                  {f.type === "bool" ? (
                    <select aria-label={fieldId(f)} value={value(f)} onChange={(e) => set(f, e.target.value)}>
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : (
                    <input aria-label={fieldId(f)} type={f.type === "int" || f.type === "float" ? "number" : "text"} step={f.type === "float" ? "any" : undefined} value={value(f)} onChange={(e) => set(f, e.target.value)} />
                  )}
                </label>
              );
            })}
          </div>
        </details>
      ))}
    </div>
  );
}
