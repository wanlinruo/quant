# 回测页 A 批（结构、统计条、主图、概览 / 收益分析 / 回撤与风险 / 参数与日志）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回测页按设计 5.3 重做结构，并上线不依赖新存储的四个标签；交易明细、持仓变化（B 批，新存储）与稳健性（#68）先显示占位。

**Architecture:** 新增只读接口 `GET /api/backtests/{id}/analysis`，在请求时由 `backtest_equity` 与本地行情（与回测同一数据源）的 QQQ / SPY 复权收盘价计算全部分析，纯函数放在 `src/quant/api/backtest_analysis.py`。不改表结构，旧回测（如服务器上的 #1）也能看。前端 `BacktestPage` 拆成列表、标题与统计条、主图、标签页几个组件。

**Tech Stack:** Python 3.12、pandas、numpy、FastAPI、React 19 + TypeScript、lightweight-charts、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 5.3、6；#67。样本内 / 样本外划分取自 `docs/superpowers/specs/2026-10-30-momentum-combo-design.md`：调参区间 2015–2022，样本外 2023-01-01 起。

## Global Constraints

- 面板只读，唯一写操作是提交回测；新接口只有 GET。
- 12 列网格、间距 12px、同排等高、表格不换行；1280px 与 1600px 下检查；切换标签时布局不动。
- 业务日期美东；年化按 252 个交易日；无风险利率按 0（与 `engine/metrics.py` 一致）。
- 计算对 2,700 个交易日的回测在 1 秒内完成（接口完成标准）。

## 决定

- **基准**：QQQ（主对照）与 SPY；价格取 `adj_close`（没有该列时用 `close`），数据源与回测一致：`data.backtest_source == "tiingo"` 用 `backtest_store(data_dir)`，否则用主 `ParquetStore(data_dir)`。基准缺数据时对照项为空，不报错。
- **样本外起点** `OOS_START = date(2023, 1, 1)` 放在 `engine/evaluation.py`。回测区间不跨这个日期时，样本内 / 样本外只显示有的那段。
- **验收标准**用 `combo_checks`：全区间、样本内、样本外的策略与 QQQ 指标由净值重新计算（`compute_metrics`）；“相邻设置”一项没有稳健性数据，状态为 `None`（前端“待稳健性检验 #68”），不当作失败。所有策略都显示这一组标准（标题注明“组合策略验收标准”）。
- **统计条**：总收益、年化、夏普、索提诺、最大回撤、卡玛、年波动 —— 每项附 QQQ；胜率、盈亏比、交易笔数需要 B 批的交易口径，先显示“—”并在提示里说明。
- **索提诺** = 年化收益 ÷（日收益中负值的标准差 × √252）。**上 / 下行捕获率**按月：QQQ 月收益为正（负）的月份，策略月收益均值 ÷ QQQ 月收益均值。**滚动 Beta** 126 日，按周取样。**滚动 12 个月**收益与夏普按月末取样。**VaR / CVaR** 95%，日收益历史分位。
- **最大 5 次回撤**：按“创新高 → 谷底 → 收复”分段，取深度最大的 5 段；未收复的恢复天数为空；同期 QQQ 收益按同一起止日期计算。
- **参数与日志**：参数与“默认”（etf_momentum 取表单默认值，其它策略取服务器配置中的策略参数，与提交回测时的来源相同）逐项比较，不同的标记；可复现信息：数据来源与截止日、覆盖率（`metrics.data`）、可复制的命令行（`uv run quant backtest --strategy … --start … --end … --no-db`）；代码版本与运行日志目前没有记录，显示“未记录（C 批）”；失败的回测显示 `error` 全文。
- **对比模式**维持现状（勾选 2–5 个时主图叠加），C 批再做完整的对比视图。

## Review Focus

1. 回测仍在排队 / 运行 / 失败：分析接口返回 `status` 与空分析，前端显示状态与失败原因，不报错。——Task 4 测试
2. 区间不跨 2023-01-01（如 2016–2020）：没有样本外段，概览只显示样本内，验收中“两个区间夏普”为 `None`。——Task 1 测试
3. 基准数据缺失（Mac 上没有 QQQ 文件）：对照列为空，策略自身的分析照常。——Task 4 测试
4. 净值只有几天（极短回测）：滚动指标、最大回撤分段为空列表，不出现 NaN（JSON 不含 NaN）。——Task 1–3 测试
5. 切换标签时主图与列表不重新加载、布局不跳动。——Task 5 测试（主图组件在标签切换时不重新挂载）

---

### Task 1: 分析纯函数（一）——序列、统计条、样本内外、年度、验收

**Files:** Create `src/quant/api/backtest_analysis.py`；Modify `src/quant/engine/evaluation.py`（`OOS_START`）；Test `tests/test_backtest_analysis.py`

**Interfaces（Produces）:**
- `OOS_START: date`
- `@dataclass Stat: key, label, value: float | None, qqq: float | None, kind: "pct" | "ratio" | "int"`
- `sortino(equity: pd.Series) -> float | None`
- `stat_bar(equity: pd.Series, qqq: pd.Series | None) -> list[Stat]`（顺序：total_return、cagr、sharpe、sortino、max_drawdown、calmar、volatility、win_rate、profit_factor、trades；后三项 value 与 qqq 均为 None）
- `split_metrics(equity, qqq) -> dict[str, dict[str, dict | None]]`：`{"full"|"ins"|"oos": {"strategy": Metrics.to_dict() | None, "qqq": ... | None}}`
- `checks(split) -> list[{"label": str, "passed": bool | None}]`（`combo_checks` 的 5 项；缺段或相邻设置为 None）
- `yearly(equity, qqq) -> list[{"year": int, "strategy": float | None, "qqq": float | None}]`

- [ ] 失败测试：合成净值（2016-01 起 1,800 个交易日、已知日收益）→ `sortino` 与手算一致；`stat_bar` 第 1 项等于 `equity[-1]/equity[0]-1`、后三项为 None；`split_metrics` 跨 2023 时三段都有、2016–2020 时 `oos` 为 None；`checks` 第 5 项为 None、区间不跨 2023 时第 4 项为 None；`yearly` 首尾年按区间内首尾计算；5 天的净值各函数不抛异常、结果可 `json.dumps(allow_nan=False)`
- [ ] 实现、通过、提交：`feat: 回测分析——统计条、样本内外、年度收益、验收标准（Refs #67）`

### Task 2: 分析纯函数（二）——收益分析

**Interfaces（Produces）:** `monthly_table(equity, qqq) -> list[{"year", "months": list[float | None] (12), "year_total", "qqq_year"}]`；`distribution(equity, bins: int = 20) -> list[{"lo", "hi", "count"}]`（月收益）；`best_worst(equity) -> list[{"period": "日"|"周"|"月"|"季"|"年", "best": float, "best_at": str, "worst": float, "worst_at": str}]`（周、月、季、年按自然周期末取样）；`rolling_12m(equity) -> list[{"date", "ret", "sharpe"}]`

- [ ] 失败测试：已知月收益的合成序列 → 月表数值、全年合计等于该年首尾；分布计数总和等于月份数；最好 / 最差日期正确；滚动 12 个月第一个点在第 12 个月末
- [ ] 实现、通过、提交：`feat: 回测分析——月度收益、分布、最好最差、滚动 12 个月（Refs #67）`

### Task 3: 分析纯函数（三）——回撤与风险

**Interfaces（Produces）:** `drawdown_episodes(equity, qqq, top: int = 5) -> list[{"start", "trough", "end": str | None, "depth", "down_days", "recovery_days": int | None, "qqq": float | None}]`；`capture(equity, qqq) -> {"up": float | None, "down": float | None}`；`risk_table(equity, qqq) -> list[{"label", "value", "qqq"}]`（年波动、下行波动、最大回撤、卡玛、Beta、相关系数、年化 Alpha、跟踪误差、信息比率）；`rolling_beta(equity, qqq, window=126) -> list[{"date", "beta"}]`；`tail(equity) -> list[{"label", "value"}]`（VaR95、CVaR95、单日最大亏损、单周最大亏损、偏度、峰度）

- [ ] 失败测试：人工构造两段回撤（-20% 收复、-10% 未收复）→ 分段起止、深度、天数正确，未收复的 `end` / `recovery_days` 为 None；与自身比较时 Beta = 1、相关 = 1、捕获率 = 1；qqq 为 None 时相关项为 None
- [ ] 实现、通过、提交：`feat: 回测分析——回撤分段、捕获率、风险表、滚动 Beta、尾部风险（Refs #67）`

### Task 4: 接口 `GET /api/backtests/{id}/analysis`

**Files:** Modify `src/quant/api/routes/backtests.py`、`src/quant/api/schemas.py`、`src/quant/api/backtests.py`（默认参数函数）；Test `tests/test_api_backtest_analysis.py`

**Interfaces（Produces）:** `BacktestAnalysis {id, strategy, status, start, end, error, initial_cash, duration_s, oos_start, stats: list[StatOut], series: {dates, strategy, qqq, spy, drawdown, qqq_drawdown}, overview: {checks, split, yearly}, returns: {monthly, distribution, best_worst, rolling}, risk: {episodes, capture, table, rolling_beta, tail}, params: list[{key, value, default, changed}], repro: {source, data_end, coverage, command}}`；未完成的回测除基础字段外为空；不存在的 id → 404

- [ ] 失败测试（测试库，`tests/api_fixtures.py` 写一条 done 回测 + 净值 + 本地 QQQ / SPY 样本日线）：返回 200、`series.dates` 与净值一致且已归一化到 1、统计条 10 项、`params` 中修改过的项 `changed`；running 的回测返回空分析；删除 QQQ 文件后对照为 None；响应 JSON 不含 NaN；耗时 < 1 秒（2,700 点）
- [ ] 实现、`make api-types`、通过、提交：`feat: 回测分析接口（Refs #67）`

### Task 5: 前端结构——列表、标题与统计条、主图、标签框架

**Files:** Modify `web/src/pages/BacktestPage.tsx`、`web/src/charts/LineChart.tsx`（`logScale?: boolean`、`shadeFrom?: string` 用直方图系列画样本外底色）、`web/src/styles/layout.css`；Create `web/src/pages/backtest/RunList.tsx`、`RunHeader.tsx`、`MainChart.tsx`；Test `web/src/pages/BacktestPage.test.tsx`

页面：左 3 列 = 列表（筛选 全部 / 完成 / 进行中 / 失败；每行状态点、编号、策略、年化、回撤；运行中显示“运行中”，失败显示原因第一行；勾选对比；“+ 新建”）；右 9 列 = 标题（编号、策略、状态、区间、初始资金、用时；按钮：参数（跳到“参数与日志”）、复制参数新建（打开抽屉并预填）、加入对比、导出 CSV）→ 统计条（10 项，每项下方小字 QQQ）→ 主图（对数 / 线性切换，策略、QQQ、SPY 三条线，样本外蓝底）+ 回撤对比 → 7 个标签（交易明细、持仓变化显示“B 批接入（新存储）”，稳健性显示“待 #68”）。

- [ ] 失败测试：筛选“失败”只剩失败的并显示原因；统计条显示“年化 12.3%”与“QQQ 10.1%”；切到“收益分析”再切回时主图组件不重新挂载（用计数 mock 验证）；交易明细标签显示“B 批接入”；点“复制参数新建”打开抽屉且策略已选好
- [ ] 实现、`make web-check`、提交：`feat: 回测页结构、统计条与主图（Refs #67）`

### Task 6: 前端标签——概览、收益分析、回撤与风险、参数与日志

**Files:** Create `web/src/pages/backtest/OverviewTab.tsx`、`ReturnsTab.tsx`、`RiskTab.tsx`、`ParamsTab.tsx`；Test 同上

- 概览：验收标准逐项（通过 / 未通过 / 待检验三种状态）、样本内 vs 样本外表（策略与 QQQ 的年化、夏普、回撤）、年度收益柱状（HTML 条形，策略与 QQQ 并排）
- 收益分析：月度热力表（12 列 + 全年 + QQQ，颜色按 ±5% 饱和）、月收益分布（HTML 柱）、最好 / 最差表、滚动 12 个月收益与夏普（折线）；子仓贡献显示“B 批接入”
- 回撤与风险：水下回撤图、最大 5 次回撤表、捕获率、风险指标表、滚动 Beta 图、尾部风险表；择时效果显示“B 批接入”
- 参数与日志：参数表（不同的标黄）、可复现信息（命令行可复制）、失败原因全文；运行日志“未记录（C 批）”

- [ ] 失败测试：每个标签至少一个关键数值或文案；验收“相邻设置”显示“待稳健性检验”；月表某格颜色为涨；参数表改过的行有标记
- [ ] 实现、`make web-check`、提交：`feat: 回测页概览、收益分析、回撤与风险、参数与日志（Refs #67）`

### Task 7: 联调与验收

- [ ] `make check` 全部通过（带 `QUANT_TEST_DATABASE_URL`）
- [ ] 本地用测试库与合成 / Mac 行情跑一个回测，1280px / 1600px 截图检查七个标签的布局（同排等高、不换行、切换不跳动）
- [ ] 部署后在服务器打开回测 #1（etf_momentum 2016–2026）核对：统计条与 `metrics` 一致、QQQ 对照有值、样本外底色从 2023 起、接口耗时；结果记入 #67
