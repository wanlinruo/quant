import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { BacktestDefaults, BacktestSubmit, SubmitResult } from "../api/types";

type Props = { defaults: BacktestDefaults; onClose: () => void; onSubmitted: (id: number) => void };

const STATUS_STYLE = { position: "fixed", top: 0, right: 0, bottom: 0, width: 440 } as const;

/** 新建回测：面板唯一的写操作，只插入一条排队任务，由 backtest-worker 执行。 */
export function BacktestSubmitDrawer({ defaults, onClose, onSubmitted }: Props) {
  const [form, setForm] = useState({
    strategy: defaults.strategies.includes("etf_momentum") ? "etf_momentum" : defaults.strategies[0],
    start: defaults.start,
    end: defaults.end,
    universe: defaults.universe,
    safe_asset: defaults.safe_asset,
    lookback: String(defaults.lookback),
    top_k: String(defaults.top_k),
    sma_window: String(defaults.sma_window),
    cash: String(defaults.cash),
    commission_per_share: defaults.commission_per_share ?? "",
    min_commission: defaults.min_commission ?? "",
    slippage: defaults.slippage,
  });
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
        <div className="muted sm" style={{ margin: "6px 0" }}>{form.strategy} 的参数沿用服务器配置（与模拟盘一致），此处不可修改。</div>
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
