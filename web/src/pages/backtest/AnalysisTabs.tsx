import { useState } from "react";
import type { BacktestAnalysis } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { Cell, Grid } from "../../components/Grid";
import { LineChart } from "../../charts/LineChart";
import { SLEEVE_COLOR, SLEEVE_TEXT } from "../../lib/backtestStatus";
import { num, pct, signedPct } from "../../lib/format";

type A = { a: BacktestAnalysis };
type M = Record<string, unknown> | null | undefined;

const p2 = (v: number | null | undefined) => (v == null ? "—" : pct(v, 2));
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");
const mnum = (m: M, k: string) => (m && typeof m[k] === "number" ? (m[k] as number) : null);

export function Sub({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <div className="subpanel">
      <div className="subpanel-h"><b>{title}</b>{note && <span className="muted sm">{note}</span>}</div>
      {children}
    </div>
  );
}

function Bar({ v, max }: { v: number | null | undefined; max: number }) {
  if (v == null || !max) return <span className="hbar" />;
  const w = Math.min(Math.abs(v) / max, 1) * 50;
  return (
    <span className="hbar">
      <i className={v >= 0 ? "pos" : "neg"} style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
    </span>
  );
}

/** 概览：验收标准逐项、样本内 vs 样本外、年度收益 */
export function OverviewTab({ a }: A) {
  const o = a.overview;
  if (!o) return <EmptyState />;
  const seg = [["full", "全区间"], ["ins", "样本内（2023 前）"], ["oos", "样本外（2023 起）"]] as const;
  const availableSeg = seg.filter(([k]) => o.split[k]?.strategy || o.split[k]?.qqq);
  const maxY = Math.max(...o.yearly.flatMap((y) => [Math.abs(y.strategy ?? 0), Math.abs(y.qqq ?? 0)]), 0.01);
  return (
    <div data-testid="tab-概览">
      <Grid>
        <Cell span={5}>
          <Sub title="组合策略验收标准" note="全区间、扣除成本">
            <table className="dt compact">
              <tbody>
                {o.checks.map((c) => (
                  <tr key={c.label}>
                    <td>{c.label}</td>
                    <td className="r">
                      {c.passed === true ? <span className="pill ok">通过</span> : c.passed === false ? <span className="pill err">未通过</span>
                        : <span className="pill hold">{c.label.startsWith("相邻") ? "待稳健性检验（#68）" : "数据不足"}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sub>
        </Cell>
        <Cell span={7}>
          <Sub title="样本内 vs 样本外" note="策略 / QQQ">
            <table className="dt compact">
              <thead><tr><th>区间</th><th className="r">年化</th><th className="r">QQQ</th><th className="r">夏普</th><th className="r">QQQ</th><th className="r">最大回撤</th><th className="r">QQQ</th></tr></thead>
              <tbody>
                {availableSeg.map(([k, label]) => {
                  const s = o.split[k]?.strategy as M;
                  const q = o.split[k]?.qqq as M;
                  return (
                    <tr key={k}>
                      <td>{label}</td>
                      <td className="r num">{s ? p2(mnum(s, "cagr")) : "—"}</td>
                      <td className="r num muted">{q ? p2(mnum(q, "cagr")) : "—"}</td>
                      <td className="r num">{s ? num(mnum(s, "sharpe"), 2) : "—"}</td>
                      <td className="r num muted">{q ? num(mnum(q, "sharpe"), 2) : "—"}</td>
                      <td className="r num">{s ? p2(mnum(s, "max_drawdown")) : "—"}</td>
                      <td className="r num muted">{q ? p2(mnum(q, "max_drawdown")) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Sub>
        </Cell>
      </Grid>
      <Sub title="年度收益" note="蓝：策略　橙：QQQ">
        <table className="dt compact">
          <tbody>
            {o.yearly.map((y) => (
              <tr key={y.year}>
                <td style={{ width: 60 }}>{y.year}</td>
                <td className={`r num ${tone(y.strategy)}`} style={{ width: 80 }}>{y.strategy == null ? "—" : signedPct(y.strategy, 1)}</td>
                <td><Bar v={y.strategy} max={maxY} /></td>
                <td className="r num muted" style={{ width: 80 }}>{y.qqq == null ? "—" : signedPct(y.qqq, 1)}</td>
                <td><span className="qbar"><Bar v={y.qqq} max={maxY} /></span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Sub>
    </div>
  );
}

function heat(v: number | null | undefined): React.CSSProperties | undefined {
  if (v == null) return undefined;
  const a = Math.min(Math.abs(v) / 0.05, 1);
  return { background: v >= 0 ? `rgba(34,197,94,${0.12 + 0.5 * a})` : `rgba(239,68,68,${0.12 + 0.5 * a})` };
}

/** 收益分析：月度热力表、月收益分布、最好 / 最差、滚动 12 个月 */
export function ReturnsTab({ a }: A) {
  const r = a.returns;
  if (!r) return <EmptyState />;
  const distributionMonths = r.distribution.reduce((sum, b) => sum + b.count, 0);
  const maxCount = Math.max(...r.distribution.map((b) => b.count), 1);
  const roll = r.rolling.map((x) => ({ time: x.date, ret: x.ret, sharpe: x.sharpe }));
  return (
    <div data-testid="tab-收益分析">
      <Sub title="月度收益" note="全年与同期 QQQ">
        <div className="table-wrap">
          <table className="dt compact heat">
            <thead><tr><th>年</th>{Array.from({ length: 12 }, (_, i) => <th key={i} className="r">{i + 1}月</th>)}<th className="r">全年</th><th className="r">QQQ</th></tr></thead>
            <tbody>
              {r.monthly.map((row) => (
                <tr key={row.year}>
                  <td>{row.year}</td>
                  {row.months.map((m, i) => <td key={i} className={`r num ${tone(m)}`} style={heat(m)}>{m == null ? "" : signedPct(m, 1)}</td>)}
                  <td className={`r num ${tone(row.year_total)}`}><b>{row.year_total == null ? "—" : signedPct(row.year_total, 1)}</b></td>
                  <td className="r num muted">{row.qqq_year == null ? "—" : signedPct(row.qqq_year, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Sub>
      <Grid>
        <Cell span={4}>
          <Sub title="月收益分布">
            {distributionMonths <= 1 ? <EmptyState note="仅有 1 个月收益，样本不足以形成分布" /> : <div className="hist">
              {r.distribution.map((b, i) => (
                <div key={i} className="hist-col" title={`${signedPct(b.lo, 1)} ~ ${signedPct(b.hi, 1)}：${b.count} 个月`}>
                  <i className={b.hi <= 0 ? "neg" : "pos"} style={{ height: `${(b.count / maxCount) * 100}%` }} />
                </div>
              ))}
            </div>}
          </Sub>
        </Cell>
        <Cell span={4}>
          <Sub title="最好 / 最差">
            <table className="dt compact">
              <thead><tr><th>周期</th><th className="r">最好</th><th>日期</th><th className="r">最差</th><th>日期</th></tr></thead>
              <tbody>
                {r.best_worst.map((x) => (
                  <tr key={x.period}>
                    <td>{x.period}</td>
                    <td className="r num up">{x.best == null ? "—" : signedPct(x.best, 1)}</td><td className="muted">{x.best_at}</td>
                    <td className="r num down">{x.worst == null ? "—" : signedPct(x.worst, 1)}</td><td className="muted">{x.worst_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sub>
        </Cell>
        <Cell span={4}>
          <Sub title="子仓累计收益贡献" note="占初始资金">
            {r.sleeves ? (
              <LineChart
                height={160}
                percent
                series={Object.entries(r.sleeves.series).map(([k, vals]) => ({
                  name: SLEEVE_TEXT[k] ?? k,
                  color: SLEEVE_COLOR[k] ?? "#787b86",
                  points: (r.sleeves?.dates ?? []).map((d, i) => ({ time: d, value: vals[i] })),
                }))}
              />
            ) : <EmptyState note="此回测运行于交易记录功能上线之前，重新运行即可查看" />}
          </Sub>
        </Cell>
      </Grid>
      <Grid>
        <Cell span={6}>
          <Sub title="滚动 12 个月收益">
            {roll.length ? <LineChart height={160} percent series={[{ name: "", color: "#2962ff", points: roll.flatMap((x) => (x.ret == null ? [] : [{ time: x.time, value: x.ret }])) }]} /> : <EmptyState />}
          </Sub>
        </Cell>
        <Cell span={6}>
          <Sub title="滚动 12 个月夏普">
            {roll.length ? <LineChart height={160} series={[{ name: "", color: "#22c55e", points: roll.flatMap((x) => (x.sharpe == null ? [] : [{ time: x.time, value: x.sharpe }])) }]} /> : <EmptyState />}
          </Sub>
        </Cell>
      </Grid>
    </div>
  );
}

/** 回撤与风险：水下回撤、最大 5 次回撤、捕获率、风险指标、滚动 Beta、尾部风险 */
export function RiskTab({ a }: A) {
  const r = a.risk;
  if (!r) return <EmptyState />;
  const s = a.series;
  return (
    <div data-testid="tab-回撤与风险">
      <Grid>
        <Cell span={7}>
          <Sub title="水下回撤">
            {s ? <LineChart height={180} percent series={[{ name: "", color: "#ef4444", points: s.dates.map((d, i) => ({ time: d, value: s.drawdown[i] })) }]} /> : <EmptyState />}
          </Sub>
        </Cell>
        <Cell span={5}>
          <Sub title="滚动 Beta" note="对 QQQ，126 日">
            {r.rolling_beta.length ? <LineChart height={180} series={[{ name: "", color: "#a855f7", points: r.rolling_beta.flatMap((x) => (x.beta == null ? [] : [{ time: x.date, value: x.beta }])) }]} /> : <EmptyState />}
          </Sub>
        </Cell>
      </Grid>
      <Sub title="最大 5 次回撤" note="同期 QQQ = 策略从高点到谷底这段时间">
        <table className="dt compact">
          <thead><tr><th>开始</th><th>谷底</th><th>收复</th><th className="r">深度</th><th className="r">下跌天数</th><th className="r">恢复天数</th><th className="r">同期 QQQ</th></tr></thead>
          <tbody>
            {r.episodes.map((e) => (
              <tr key={e.start}>
                <td>{e.start}</td><td>{e.trough}</td><td>{e.end ?? <span className="muted">未收复</span>}</td>
                <td className="r num down">{p2(e.depth)}</td><td className="r num">{e.down_days}</td>
                <td className="r num">{e.recovery_days ?? "—"}</td><td className={`r num ${tone(e.qqq)}`}>{p2(e.qqq)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Sub>
      <Grid>
        <Cell span={4}>
          <Sub title="涨跌市中的表现" note="按月收益的算术均值比（策略 ÷ QQQ）">
            <table className="dt compact kv">
              <tbody>
                <tr><td className="muted">上行捕获率</td><td className="r num">{r.capture.up == null ? "—" : pct(r.capture.up, 0)}</td></tr>
                <tr><td className="muted">下行捕获率</td><td className="r num">{r.capture.down == null ? "—" : pct(r.capture.down, 0)}</td></tr>
              </tbody>
            </table>
            <div className="subpanel-h" style={{ marginTop: 8 }}><b>择时减仓</b><span className="muted sm">少亏 = 实际 − 系数为 1 的估算</span></div>
            {r.timing?.length ? (
              <table className="dt compact">
                <thead><tr><th>时段</th><th className="r">天数</th><th className="r">系数</th><th className="r">少亏</th></tr></thead>
                <tbody>
                  {r.timing.map((t) => (
                    <tr key={t.start}>
                      <td>{t.start} → {t.end}</td><td className="r num">{t.days}</td><td className="r num">{num(t.factor, 2)}</td>
                      <td className={`r num ${tone(t.saved)}`}>{t.saved == null ? "—" : signedPct(t.saved, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="muted sm">没有择时减仓（或此回测运行于记录功能上线之前）</div>}
          </Sub>
        </Cell>
        <Cell span={4}>
          <Sub title="风险指标" note="策略 / QQQ">
            <table className="dt compact kv">
              <tbody>
                {r.table.map((x) => (
                  <tr key={x.label}>
                    <td className="muted">{x.label}</td>
                    <td className="r num">{x.value == null ? "—" : ["Beta", "相关系数", "卡玛", "信息比率"].includes(x.label) ? num(x.value, 2) : pct(x.value, 2)}</td>
                    <td className="r num muted">{x.qqq == null ? "" : ["卡玛"].includes(x.label) ? num(x.qqq, 2) : pct(x.qqq, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sub>
        </Cell>
        <Cell span={4}>
          <Sub title="尾部风险">
            <table className="dt compact kv">
              <tbody>
                {r.tail.map((x) => (
                  <tr key={x.label}>
                    <td className="muted">{x.label}</td>
                    <td className="r num">{x.value == null ? "—" : ["偏度", "超额峰度"].includes(x.label) ? num(x.value, 2) : pct(x.value, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sub>
        </Cell>
      </Grid>
    </div>
  );
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "—";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** 参数与日志：参数（本次 vs 默认，不同的标黄）、可复现信息、失败原因 */
export function ParamsTab({ a }: A) {
  const rp = a.repro;
  return (
    <div data-testid="tab-参数与日志">
      <Grid>
        <Cell span={6}>
          <Sub title="策略参数" note="本次 vs 默认，不同的标黄">
            {a.params?.length ? (
              <table className="dt compact">
                <thead><tr><th>参数</th><th className="r">本次</th><th className="r">默认</th></tr></thead>
                <tbody>
                  {(a.params ?? []).map((p) => (
                    <tr key={p.key} className={p.changed ? "changed" : undefined}>
                      <td>{p.key}</td><td className="r num">{show(p.value)}</td><td className="r num muted">{show(p.default)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <EmptyState note="使用策略默认参数" />}
          </Sub>
        </Cell>
        <Cell span={6}>
          <Sub title="回测设置与可复现信息">
            <table className="dt compact kv">
              <tbody>
                <tr><td className="muted">数据来源</td><td className="r">{rp?.source ?? "—"}</td></tr>
                <tr><td className="muted">数据截止</td><td className="r">{rp?.data_end ?? "—"}</td></tr>
                <tr><td className="muted">候选池覆盖率</td><td className="r">{typeof rp?.coverage === "number" ? pct(rp.coverage, 1) : "—"}</td></tr>
                <tr><td className="muted">代码版本</td><td className="r num">{rp?.code_version ?? <span className="muted">未记录（此功能上线前的回测）</span>}</td></tr>
              </tbody>
            </table>
            {rp && <pre className="cmd" title="可复制的命令行">{rp.command}</pre>}
            {rp && <div className="muted sm">命令行只含佣金与滑点；平台费、费用封顶等其它成本取运行时的配置</div>}
          </Sub>
        </Cell>
      </Grid>
      <RunLog error={a.error} lines={rp?.log ?? []} />
    </div>
  );
}

function RunLog({ error, lines }: { error: string | null | undefined; lines: string[] }) {
  const [warnOnly, setWarnOnly] = useState(false);
  const shown = warnOnly ? lines.filter((x) => /\b(WARNING|ERROR|CRITICAL)\b/.test(x)) : lines;
  return (
    <Sub title="运行日志" note={lines.length ? `${lines.length} 行` : undefined}>
      {error ? <pre className="cmd err">{error}</pre> : null}
      {lines.length ? (
        <>
          <label className="sm"><input type="checkbox" checked={warnOnly} onChange={(e) => setWarnOnly(e.target.checked)} /> 只看警告</label>
          <pre className="cmd log">{shown.join("\n") || "（没有警告）"}</pre>
        </>
      ) : <div className="muted sm">没有运行日志（此功能上线前的回测）</div>}
    </Sub>
  );
}
