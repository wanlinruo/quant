# 面板改版第 2 步：组合页与个股详情页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计 5.2、5.4、7.4 实现组合页与个股详情页（只用现有数据与计算），并补上实盘侧记录：信号与任务按账户区分、信号按子仓拆开并带原因、任务尝试记录、财报日历覆盖全部持仓。Refs #65。

**Architecture:** 后端先补记录（Alembic 迁移 + scheduler 写入，信号计算本身不变），再在 `src/quant/api/` 增加纯计算模块 `analytics.py`（无 I/O，可单测）和两组只读接口（组合、个股），最后重写两个前端页面。所有接口按 `mode` 过滤；dashboard 不持有券商凭证、不跑策略、不下单。

**Tech Stack:** Python 3.12、SQLAlchemy 2 + Alembic、FastAPI、pandas；React 19 + TypeScript、lightweight-charts 5、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md`（第 5.2、5.4、5.6、6、7.4 节）；原型 `docs/superpowers/specs/2026-10-31-dashboard-redesign-prototype.html`（`#p-portfolio`、`#p-stock`）。

## Global Constraints

- 面板只读，唯一写操作是提交回测；`tests/test_api_readonly.py` 必须继续通过（`src/quant/api/` 不得导入 `quant.broker`、`tigeropen`、`quant.core.safety`、`quant.engine.executor`，不得出现 `place_order`、`plan_orders` 等名字）。
- 表结构只能通过 Alembic 迁移修改；迁移必须可 `downgrade`。
- 7.4 的改动“只增加记录，不改变信号计算、风控和下单逻辑”：`Combo.target_weights` 的返回值对同一输入逐位不变。
- 策略纯计算、无 I/O；回测与实盘共用同一个策略类，不得出现第二套计算路径。
- 业务时间一律美东时间；交易日以 `XNYS` 日历为准。
- 布局（设计第 6 节）：12 列网格，一排只用 12、6+6、8+4、4+8、4+4+4、2×6、3×4、9+3、5+4+3；间距 12px；同排等高；表格 `white-space: nowrap`、表头固定、内部滚动；在 1280px 与 1600px 下检查对齐。
- 颜色：绿涨红跌；模拟盘绿色、实盘橙色 `#f97316`，橙色不用于涨跌；数字 `tabular-nums`，百分比带正负号。
- 没有数据的模块显示“暂无数据”；依赖未接入数据源的模块显示“数据接入中”（板块分布 #61、估值与财务和新闻 #60、行业 ETF 对比 #62、回测中的表现 #67）。
- 不得把密钥、账户号写入代码、日志或接口返回。

## 我做的决定（实现时照此执行，用户审阅计划时可推翻）

1. **旧数据的 `mode`**：迁移把已有 `signals`、`job_runs` 行的 `mode` 回填为 `paper`（服务器上唯一的真实数据是模拟盘；本地开发库数据可丢弃）。
2. **下次调仓预览**不在接口里跑策略：用该账户最近一条信号的目标权重 vs 最新持仓快照的实际权重。daily_signal 与 `quant preview` 用同一个 `build_context` + `strategy.run`，信号表里存的就是同一逻辑的结果；页面注明“基于 {signal_date} 收盘信号”。佣金按配置的佣金与平台费公式估算（接口不得导入 `quant.broker`），页面标“估算”。
3. **实际权重的分母**用账户净值（`net_liquidation`），现金单列。
4. **信号状态**阈值：波段仓——收盘价相对 10 日线 > +3% 绿、0~+3% 黄、低于 10 日线红；动量仓——排名 ≤ top_n−2 绿、top_n−1~top_n 黄、> top_n 或不在排名中红（下次月度调仓会卖出）。
5. **每日盈亏** = 相邻两日账户净值之差（不区分出入金，Phase 4 起没有出入金）；**收益贡献** = 前一日持仓数量 × 当日收盘价变化（不复权价格），按子仓拆分时按信号中两仓权重比例分摊。
6. **持有天数 / 持仓期** 从 `position_snapshots` 推出：同一 symbol 在连续快照日中出现的区间。
7. **候选池股票的日线**：主数据目录没有时回落到候选池目录（`universe_bars`，未复权），接口返回 `adjusted: false`，页面在图表标题旁标“未复权”。
8. **“属于哪些指数”** 只判断是否为当前标普 500 成分股（现有成分股文件）；股票名称、交易所、盘后价没有数据源，不显示。
9. **动量排名**：信号明细只存前 50 名与候选池总数（足够判断持有与“接近退出”）；个股不在前 50 名显示“50 名之外”。
10. **“每个标的的原因说明”**（设计 7.4）：信号表只存结构化明细（子仓权重、排名、择时系数），原因文案由接口按明细生成（见 Task 5）。文案改动不需要迁移，也不会让历史信号与新信号说法不一致。
11. **不在本计划**：券商连接状态、接口延迟、数据源额度用量的记录（7.4 末段）只服务于系统页，随 #71 实施；组合页“按板块分布”、个股页估值 / 新闻 / 同板块 / 行业 ETF 等待 #60–#62；“回测中的表现”等待 #67。

## Review Focus

- 账户只有 1 个快照日（刚启用）：收益率、回撤、Beta、VaR 等需要序列的指标返回 `null`，页面显示“数据不足”，不报错、不显示 NaN/Infinity。→ Task 4 `test_metrics_with_short_history`；Task 7 `shows 数据不足 for null metrics`。
- 持仓里有不在任何数据目录中的代码（如被退市或候选池未同步）：持仓表该行价格与今日涨跌显示 “—”，整页不 500。→ Task 5 `test_holdings_missing_price`。
- 信号明细为空（非 combo 策略或迁移前的旧信号）：子仓、原因、动量排名显示 “—”，择时系数卡显示“暂无数据”。→ Task 5 `test_holdings_without_signal_detail`。
- 个股页打开一个从未持有、不在候选池、也没有日线的合法代码（如 `ZZZZ`）：返回 200，页面显示“暂无数据”而不是一直加载。→ Task 6 `test_stock_profile_unknown_symbol`；Task 8 `renders empty stock`。
- 切换到实盘（无数据）时组合页所有接口返回空结果，不串用模拟盘的信号或持仓。→ Task 5 `test_portfolio_endpoints_filter_by_mode`。

---

### Task 1: 迁移——信号与任务按账户区分，信号明细，任务尝试记录

**Files:**
- Modify: `src/quant/db/models.py`（`JobRun`、`Signal`，新增 `JobAttempt`）
- Create: `src/quant/db/migrations/versions/20261101_<rev>_mode_and_job_attempts.py`（`uv run alembic revision --autogenerate -m "mode and job attempts"` 生成后手工补回填与约束名）
- Test: `tests/test_models.py`、`tests/test_db.py`（沿用现有迁移测试方式）

**Interfaces:**
- Produces:
  - `JobRun.mode: Mapped[str] = String(8)`，唯一约束改为 `("mode", "job_name", "trade_date")`
  - `Signal.mode: Mapped[str] = String(8)`；`Signal.detail: Mapped[dict[str, Any] | None] = JSONB, nullable`；唯一约束改为 `("mode", "strategy", "signal_date")`
  - `JobAttempt`（表 `job_attempts`）：`id`、`mode String(8)`、`job_name String(64)`、`trade_date Date`、`attempt Integer`、`started_at`、`finished_at | None`、`result String(16)`（`success`/`failed`/`running`）、`error Text | None`；唯一约束 `("mode", "job_name", "trade_date", "attempt")`，索引 `("mode", "trade_date")`

- [ ] **Step 1: 写失败测试** `test_signal_unique_per_mode`（同一 strategy、signal_date，`paper` 与 `live` 各一行可共存，同 mode 重复插入抛 `IntegrityError`）、`test_job_run_unique_per_mode`（同理）、`test_job_attempts_roundtrip`、`test_migration_backfills_paper`（降到上一版本插入旧行，升级后 `mode == "paper"`，再 `downgrade` 成功）。带 `@pytest.mark.db` 与 `needs_db`。
- [ ] **Step 2: 运行** `uv run pytest tests/test_models.py tests/test_db.py -q` — Expected: 新测试 FAIL（列不存在）。
- [ ] **Step 3: 改模型并生成迁移**：先加可空列、`UPDATE ... SET mode = 'paper'`、再改为 `NOT NULL`；删除旧唯一约束、建新约束；`downgrade` 反向（删除新表与列、恢复旧约束；若存在同 strategy+日期的多 mode 行，降级前只保留 `paper`）。
- [ ] **Step 4: 运行** 同上 — Expected: PASS；`uv run alembic check` 无差异。
- [ ] **Step 5: Commit** `feat: 信号与任务记录按账户区分，新增任务尝试记录（Refs #65）`

### Task 2: 策略输出子仓明细；每日信号写入账户与明细

**Files:**
- Modify: `src/quant/strategies/base.py`（新增 `run_explained`）、`src/quant/strategies/combo.py`（`breakdown`）、`src/quant/jobs/daily.py`（`daily_signal`、上一信号查询带 `mode`）、`src/quant/jobs/scheduler.py`（读取 Signal 处带 `mode`）、`src/quant/cli.py`（`preview` 改用 `run_explained` 打印同一份明细，删除其中重复的择时 / 排名计算）
- Test: `tests/test_combo.py`、`tests/test_jobs.py`、`tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 的 `Signal.mode`、`Signal.detail`
- Produces:
  - `Strategy.run_explained(self, ctx: Context) -> tuple[dict[str, float], dict[str, Any] | None]`：默认 `(self.run(ctx), None)`
  - `@dataclass(frozen=True) class ComboBreakdown: momentum: dict[str, float]; swing: dict[str, float]; factor: float; weights: dict[str, float]; rebalance_day: date; ranking: list[str]; universe_size: int`
  - `Combo.breakdown(self, ctx: Context) -> ComboBreakdown`；`Combo.target_weights` 改为 `return self.breakdown(ctx).weights`（唯一计算路径）
  - `Combo.run_explained` 返回 `(validate_weights(b.weights), detail)`，detail 结构固定为：
    ```
    {"sleeves": {"momentum": {...子仓内权重}, "swing": {...}},
     "sleeve_weights": {"momentum": 0.4, "swing": 0.6},
     "timing_factor": 1.0, "vol_symbol": "QQQ",
     "momentum_rank": ["NVDA", ...前 50], "universe_size": 503,
     "rebalance_day": "2026-10-30", "top_n": 10}
    ```
  - `daily_signal` 写入 `mode=ctx.mode`、`detail`；冲突键 `(mode, strategy, signal_date)`

- [ ] **Step 1: 写失败测试**：`test_breakdown_matches_target_weights`（用 `tests/test_combo.py` 现有夹具，`breakdown(ctx).weights == Combo().target_weights(ctx)` 且 `merge_weights(b.momentum, b.swing, 0.4, 0.6, b.factor) == b.weights`）、`test_run_explained_detail_shape`（键与上面一致，`len(momentum_rank) <= 50`）、`test_daily_signal_writes_mode_and_detail`（`tests/test_jobs.py` 现有数据库夹具，mode=`sim` 写入后 `Signal.mode == "sim"` 且 `detail["timing_factor"]` 存在；另一 mode 的同日信号不被覆盖）、`test_preview_prints_breakdown`（输出含“择时系数”“动量排名”）。
- [ ] **Step 2: 运行** `uv run pytest tests/test_combo.py tests/test_jobs.py tests/test_cli.py -q` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**。`ranking` 用 `self._momentum.ranking(ctx, me, p)`（已缓存，不重复计算）；`momentum_weight == 0` 时 `ranking=[]`、`universe_size=0`。
- [ ] **Step 4: 运行** 同上，以及 `uv run pytest tests/test_research_combo.py tests/test_backtester.py -q` — Expected: 全部 PASS（回测结果不变）。
- [ ] **Step 5: Commit** `feat: 组合策略输出子仓明细，每日信号按账户写入（Refs #65）`

### Task 3: 任务尝试记录与账户；财报日历覆盖全部持仓

**Files:**
- Modify: `src/quant/jobs/runner.py`（`run_job`、`job_status`、`_finish` 增加 `mode`；每次执行写 `JobAttempt`）、`src/quant/jobs/scheduler.py`、`src/quant/cli.py`、`src/quant/jobs/daily.py`（`job_status` 调用带 `ctx.mode`；`sync_strategy_extras` 的财报标的扩大）
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: Task 1 的 `JobRun.mode`、`JobAttempt`；Task 2 的 `Signal.mode`
- Produces:
  - `job_status(session: Session, name: str, day: date, mode: str) -> str | None`
  - `run_job(engine, name, day, fn, notifier, *, mode: str, rerun_failed=False, quiet=(JobStopped,)) -> str`（`mode` 为必填关键字参数）
  - `earnings_watchlist(session: Session, mode: str, strategy_symbols: Sequence[str]) -> list[str]`（在 `daily.py`）：`strategy_symbols` ∪ 该 mode 最新持仓快照的 symbol ∪ 该 mode 最新信号的目标 symbol，排序去重

- [ ] **Step 1: 写失败测试**：`test_run_job_records_attempts`（失败一次后 `rerun_failed=True` 成功：`job_attempts` 两行，attempt 1 `failed` 带错误首行，attempt 2 `success`；`job_runs` 仍只有一行）、`test_run_job_isolated_by_mode`（`paper` 已成功不影响 `live` 同日执行）、`test_earnings_watchlist_includes_positions_and_targets`、`test_missing_earnings_warning_only_for_strategy_symbols`（持仓里的 ETF 没有财报日不产生提示）。
- [ ] **Step 2: 运行** `uv run pytest tests/test_jobs.py -q` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**。attempt 序号 = 该 (mode, job, day) 已有尝试数 + 1；`_finish` 同时更新 `job_runs` 与本次 attempt；`error` 只存首行（≤ 500 字符）。`_sync_earnings` 只对 `strategy.earnings_symbols(params)` 计算“缺财报日”提示。
- [ ] **Step 4: 运行** `uv run pytest tests/test_jobs.py tests/test_cli.py tests/test_cli_ops.py -q` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 记录每次任务尝试，财报日历覆盖全部持仓与目标（Refs #65）`

### Task 4: 纯计算模块 analytics

**Files:**
- Create: `src/quant/api/analytics.py`
- Test: `tests/test_api_analytics.py`

**Interfaces:**
- Produces（全部纯函数，输入 pandas 对象或 dataclass，无 I/O；不足以计算时返回 `None`）：
  - `daily_returns(values: pd.Series) -> pd.Series`
  - `drawdown(values: pd.Series) -> pd.Series`（≤ 0）
  - `summary(nav: pd.Series, qqq: pd.Series, spy: pd.Series) -> PortfolioSummary`：`today_pnl`、`today_return`、`total_return`、`qqq_return`、`spy_return`（同区间）、`max_drawdown`、`current_drawdown`、`ann_vol`、`sharpe`（无风险利率 0，至少 20 个收益率）
  - `risk(nav: pd.Series, spy: pd.Series, qqq: pd.Series, window: int = 60) -> RiskStats`：`beta_spy`、`corr_qqq`、`var95_1d`（历史法，金额，正数表示可能亏损）、至少 20 个对齐收益率，否则各项 `None`
  - `concentration(weights: Mapping[str, float]) -> tuple[float, float]`：前 5 合计、最大单一
  - `pnl_calendar(nav: pd.Series, year: int, month: int) -> list[CalendarDay]`（`date`、`pnl`、`ret`）
  - `contributions(qty: pd.DataFrame, close: pd.DataFrame, start: date, end: date) -> dict[str, float]`（`qty` 为快照日×symbol 的前一日持仓数量）
  - `closed_trades(fills: Sequence[FillLike]) -> list[ClosedTrade]`：按 symbol FIFO 配对，`ClosedTrade(symbol, open_date, close_date, qty, open_price, close_price, pnl, ret, days)`；`trade_stats(trades) -> TradeStats`（`win_rate`、`avg_pnl`、`count`）
  - `holding_periods(snapshots: Mapping[date, set[str]], symbol: str) -> list[tuple[date, date]]`；`holding_days(snapshots, symbol) -> int`
  - `signal_state(sleeve: str, close: float | None, ma10: float | None, rank: int | None, top_n: int) -> Literal["ok", "near", "exit"] | None`（阈值见“我做的决定”第 4 条）
  - `estimate_commission(qty: int, price: float, costs: CostSettings) -> float`（佣金与平台费各自取最低值、各自不超过成交额 × `max_fee_pct`；与 `CostSettings` 文档描述一致）
  - `sma(close, n)`、`rsi(close, n=14)`（复用 `quant.strategies.swing_rules.rsi`）、`atr_pct(high, low, close, n=14)`、`period_returns(close, as_of) -> dict[str, float | None]`（键 `1D`、`1W`、`1M`、`3M`、`6M`、`YTD`、`1Y`）

- [ ] **Step 1: 写失败测试**（手算的小样本，数值写死）：`test_summary_basic`（nav `[100, 110, 99]` → `total_return == -0.01`、`max_drawdown == -0.1`、`current_drawdown == -0.1`、`today_pnl == -11`）、`test_metrics_with_short_history`（单点 nav：`summary` 各收益相关字段 `None`，`risk` 全 `None`，不出现 NaN/inf）、`test_var_and_beta`（构造 spy 收益 r、组合收益 2r → `beta_spy ≈ 2`）、`test_pnl_calendar_month`、`test_contributions_uses_previous_qty`、`test_closed_trades_fifo_partial`（买 10@100、买 10@110、卖 15@120 → 一笔 10 股盈利 200 与一笔 5 股盈利 50，剩余 5 股不计）、`test_holding_periods_split_on_gap`、`test_signal_state_thresholds`（`swing` close/ma10 = 1.04 → ok、1.01 → near、0.99 → exit；`momentum` top_n=10：rank 8 → ok、9/10 → near、11/None → exit）、`test_estimate_commission_matches_cost_model`（与 `quant.broker.sim.CostModel` 在 3 组 (qty, price) 上结果相等——测试可以导入 broker，接口代码不行）、`test_period_returns`。
- [ ] **Step 2: 运行** `uv run pytest tests/test_api_analytics.py -q` — Expected: FAIL（模块不存在）。
- [ ] **Step 3: 实现**。所有比率在分母为 0 或样本不足时返回 `None`，返回前把 `nan`/`inf` 转为 `None`。
- [ ] **Step 4: 运行** 同上 + `uv run pytest tests/test_api_readonly.py -q` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 面板组合与个股指标的纯计算模块（Refs #65）`

### Task 5: 组合页接口

**Files:**
- Modify: `src/quant/api/schemas.py`、`src/quant/api/queries.py`、`src/quant/api/routes/portfolio.py`、`src/quant/api/deps.py`（`get_universe_store()`、`get_events()`）
- Test: `tests/test_api_portfolio.py`（新）、`tests/api_fixtures.py`（`seed` 增加两日 QQQ/SPY 样本日线写入工具、带 `detail` 的信号）

**Interfaces:**
- Consumes: Task 1–2 的表与 detail 结构；Task 4 全部函数
- Produces（全部 `GET`，`{mode}` 为 `Mode`）：
  - `/api/portfolio/{mode}/overview` → `PortfolioOverview`：`as_of`、`net_liquidation`、`cash`、`summary: PortfolioSummary`、`risk: RiskStats`、`top5`、`max_single`、`exposure`（持仓市值 / 净值）、`timing: TimingOut | None`（`factor`、`vol_symbol`、`vol_20d`、`threshold`）、`allocation: list[SleeveAlloc]`（`sleeve`：`momentum`/`swing`/`cash`，`actual`、`target`）、`earnings_next_week: list[str]`、`reconcile: ReconcileOut`（`trading_enabled` 来自 `system_flags`，`last_report_status` 来自该 mode 的 `daily_report` 最近一次 `job_runs`）
  - `/api/portfolio/{mode}/performance` → `PerformanceOut`：`points: list[PerfPoint]`（`date`、`portfolio`、`qqq`、`spy` 均以首日 = 1 归一，`drawdown`、`nav`）、`rebalance_dates: list[date]`（该 mode 目标权重与前一信号不同的 `signal_date`）
  - `/api/portfolio/{mode}/holdings` → `list[HoldingRow]`：`symbol`、`sleeve`（`momentum`/`swing`/`both`/`exit`/`null`）、`qty`、`avg_cost`、`price | None`、`change_pct | None`、`market_value`、`weight`、`target_weight`、`deviation`、`unrealized_pnl | None`、`state | None`、`state_note`（如“距 10 日线 +2.1%”“排名 9/10”）、`next_earnings | None`、`holding_days`
  - `/api/portfolio/{mode}/contributions?period=day|month` → `list[ContributionRow]`（`symbol`、`sleeve`、`pnl`）
  - `/api/portfolio/{mode}/calendar?month=YYYY-MM` → `list[CalendarDay]`；`month` 缺省为最新快照所在月，格式错误 422
  - `/api/portfolio/{mode}/rebalance-preview` → `RebalancePreview`：`signal_date | None`、`rows: list[PreviewRow]`（`side`、`symbol`、`sleeve`、`weight_from`、`weight_to`、`reason`）、`turnover`、`est_commission`；只列 `abs(Δw) >= 策略 min_weight_diff`（取不到则 0.01）的行
  - `/api/portfolio/{mode}/closed-trades` → `ClosedTradesOut`：`trades`、`stats`（`win_rate`、`avg_pnl`、`avg_slippage`：成交价相对订单 `reference_price` 的平均偏差，买入为正表示不利）
  - `/api/signals/latest?mode=` → 只返回该 mode 的信号；`SignalOut` 增加 `detail: dict[str, Any] | None`
  - 原因文案（`reason`）：动量仓买入“动量排名第 {r}”、卖出“排名跌出前 {top_n}”；波段仓买入“波段进场信号”、卖出“波段离场”；仅权重变化且择时系数 < 1 “择时减仓”，> 前一信号 “择时恢复”

- [ ] **Step 1: 写失败测试**（数据库夹具）：`test_overview_fields`、`test_performance_normalized_and_rebalance_dates`、`test_holdings_with_signal_detail`（NVDA 同时在两仓 → `sleeve == "both"`）、`test_holdings_without_signal_detail`、`test_holdings_missing_price`、`test_calendar_bad_month_is_422`（`month=2026-13` → 422，中文错误含“月份”）、`test_rebalance_preview_reasons`、`test_closed_trades_stats`、`test_portfolio_endpoints_filter_by_mode`（只给 paper 造数据，`live` 下 overview 的 `net_liquidation is None`、其余列表为空）、`test_signals_latest_by_mode`。
- [ ] **Step 2: 运行** `uv run pytest tests/test_api_portfolio.py -q` — Expected: FAIL。
- [ ] **Step 3: 实现**。查询放 `queries.py`，计算调用 `analytics`；QQQ/SPY 用主目录 `adj_close`；价格取主目录最近收盘，缺失回落候选池目录，再缺失为 `None`。`FIELD_LABELS` 补 `month: "月份"`、`period: "区间"`。
- [ ] **Step 4: 运行** `uv run pytest tests/test_api_portfolio.py tests/test_api_routes.py tests/test_api_readonly.py -q`，`make api-types` — Expected: PASS；`web/src/api/types.ts` 更新。
- [ ] **Step 5: Commit** `feat: 组合页接口——概览、净值对比、持仓、贡献、日历、调仓预览、已平仓（Refs #65）`

### Task 6: 个股页接口

**Files:**
- Modify: `src/quant/api/schemas.py`、`src/quant/api/queries.py`、`src/quant/api/routes/market.py`（日线回落候选池目录）、新建 `src/quant/api/routes/stock.py` 并在 `app.py` 注册
- Test: `tests/test_api_stock.py`（新）

**Interfaces:**
- Consumes: Task 4 函数；Task 2 detail；`load_events`、`load_constituents`、`universe_store`
- Produces：
  - `/api/market/bars/{symbol}` 返回体改为 `BarsOut`：`adjusted: bool`、`bars: list[Bar]`（前端 StockPage 同步修改）
  - `/api/stock/{symbol}/profile?mode=` → `StockProfile`：
    - `symbol`、`as_of | None`、`quote | None`（`close`、`change`、`change_pct`、`open`、`high`、`low`、`volume`、`turnover`、`high_52w`、`low_52w`、`ret_1m`、`ytd`）
    - `relation`：`held: bool`、`weight | None`、`sleeve | None`、`momentum_rank | None`、`universe_size | None`、`in_sp500: bool`、`swing_universe: bool`、`prev_holding | None`、`next_holding | None`（按权重排序的相邻持仓）
    - `periods: dict[str, float | None]` 与 `periods_qqq`（同键）
    - `key_stats`：`volume_ratio`（当日量 / 20 日均量）、`beta_1y`（对 SPY）、`vol_20d`、`from_high`、`to_ma120`、`next_earnings | None`、`days_to_earnings | None`（交易日）
    - `technicals`：`rsi14`、`atr14_pct`、`rs_20d`、`rs_60d`（相对 QQQ 超额收益）、`ma10`、`ma20`、`ma120`、`dist_ma10`、`dist_ma20`、`dist_ma120`
    - `swing`：`trend_up: bool | None`（收盘 > MA120 且 MA120 较 10 日前上升）、`above_support: bool | None`（收盘 ≥ MA20）、`dist_exit: float | None`（相对 MA10）；参数取 `quant.strategies.combo.V2_SWING`
    - `rank_history: list[RankPoint]`（该 mode 近 60 条信号中本股票在 `momentum_rank` 的名次，不在前 50 为 `None`）
    - `earnings`：`next | None`、`held_through: bool`、`history: list[EarningsMove]`（最近 8 个已过反应日：`date`、`move` = 反应日收盘 / 前一交易日收盘 − 1）
    - `holding_periods: list[Period]`、`trades: list[FillOut]`（该 mode 该 symbol 的成交）
  - 不合法代码（`is_valid_symbol` 为假）→ 200 且除 `symbol` 外全空

- [ ] **Step 1: 写失败测试**：`test_stock_profile_held_symbol`（持有 NVDA：`relation.held`、`sleeve`、`holding_periods` 一段、`trades` 一笔）、`test_stock_profile_unknown_symbol`（`ZZZZ` → 200，`quote is None`）、`test_stock_profile_invalid_symbol`（`^VIX` → 200 全空）、`test_bars_fallback_to_universe_store`（只在候选池目录写入 → `adjusted is False` 且有数据）、`test_earnings_history_moves`、`test_rank_history_by_mode`。
- [ ] **Step 2: 运行** `uv run pytest tests/test_api_stock.py -q` — Expected: FAIL。
- [ ] **Step 3: 实现**；`test_api_readonly.py` 与 `openapi` 生成器需覆盖新路由。
- [ ] **Step 4: 运行** `uv run pytest tests/test_api_stock.py tests/test_api_routes.py tests/test_api_readonly.py tests/test_api_app.py -q`，`make api-types` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 个股页接口——行情摘要、与我的系统的关系、技术面、财报、持仓期与成交（Refs #65）`

### Task 7: 组合页前端

**Files:**
- Modify: `web/src/pages/PortfolioPage.tsx`、`web/src/pages/PortfolioPage.test.tsx`、`web/src/styles/layout.css`
- Create: `web/src/components/Donut.tsx`（SVG 环形图）、`web/src/components/DeviationBar.tsx`、`web/src/components/PnlCalendar.tsx`、`web/src/components/Tabs.tsx`、`web/src/components/StatCard.tsx`（带“实盘”字样与副标题；替换页面内的 `Stat` 用法）

**Interfaces:**
- Consumes: Task 5 全部接口与生成的类型；`useShell().account`
- Produces: `StatCard({ label, value, sub, live })`、`Tabs({ tabs: {key,label}[], value, onChange })`（Task 8 复用）

布局（12 列）：
1. 6 个指标卡 2×6：净值（今日盈亏）、累计收益（对比 QQQ、SPY）、最大回撤（当前回撤）、年化波动 / 夏普、仓位（现金）、择时系数（QQQ 20 日波动 vs 阈值）——账户为实盘时**每张**卡标题带“实盘”
2. 净值走势 8 + 分仓与择时 4：组合（蓝）vs QQQ（紫虚线）vs SPY（灰虚线），竖线标调仓日；下方回撤；区间 1周/1月/3月/全部；收益率 / 金额切换。右侧环形图（动量、波段、现金实际 vs 目标）+ 择时距阈值；“按板块分布”显示“数据接入中”
3. 持仓 12：列按 `HoldingRow`，偏差条、信号状态点（绿/黄/红）+ `state_note`，子仓筛选（全部 / 波段仓 / 动量仓），代码可点击进入 `/stock/:symbol`（保留 `?account=`）
4. 收益贡献 4（今日 / 本月 / 按子仓）+ 每日盈亏日历 4（‹ › 切月，上涨 / 下跌天数、最好 / 最差）+ 风险 4（Beta、相关性、VaR、前 5 集中度、最大单一、下周有财报、对账状态）
5. 下次调仓预览 6（“只读预览，面板不能下单”“基于 {signal_date} 收盘信号”“佣金为估算”）+ 订单与成交 6（订单 / 成交 / 已平仓 三个标签；已平仓上方胜率、平均盈亏、平均滑点）

- [ ] **Step 1: 写失败测试**：`renders six stat cards with live label`（account=`live` 时 6 张卡都含“实盘”）、`shows 数据不足 for null metrics`、`filters holdings by sleeve`、`holding symbol links to stock page with account`（`/stock/NVDA?account=paper`）、`calendar switches month`（点 ‹ 后请求 `month=2026-09`）、`preview shows read-only notice`、`closed trades tab shows stats`、`sector distribution shows 数据接入中`。
- [ ] **Step 2: 运行** `cd web && npx vitest run src/pages/PortfolioPage.test.tsx` — Expected: FAIL。
- [ ] **Step 3: 实现**。图表复用 `LineChart`（需要支持虚线与竖线标记时在 `LineChart` 增加 `dashed?: boolean` 与 `markers?: string[]` 属性）；环形图、日历用 SVG / CSS，不新增依赖。
- [ ] **Step 4: 运行** `make web-check` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 组合页按设计重做（Refs #65）`

### Task 8: 个股详情页前端

**Files:**
- Modify: `web/src/pages/StockPage.tsx`、`web/src/pages/StockPage.test.tsx`、`web/src/charts/CandleChart.tsx`（持仓期底色、买卖点与财报日标记、十字光标读数）
- Create: `web/src/components/RangeBar.tsx`（52 周区间条）

**Interfaces:**
- Consumes: Task 6 接口；Task 7 的 `Tabs`、`StatCard`

布局：报价头部 12（代码、收盘价、涨跌、与我的系统的关系标签、“返回”与“上一只 / 下一只持仓”、开高低收、量、额、52 周区间、1 月 / YTD）；主区 9 + 右栏 3。
- 主区：区间表现（1日…1年，对比 QQQ；行业 ETF 一列显示“数据接入中”）→ 日线图（MA10/20/120、成交量、持仓期底色、买卖点、财报日；未复权时标题旁“未复权”）→ 标签：我的策略（波段信号 4 + 动量排名走势 4 + 技术面 4；交易记录 12，点一行在图上定位）、新闻（数据接入中）、财报（历史 8 次反应日涨跌）、回测中的表现（显示“随回测页上线”，见 #67）
- 右栏：关键数据（52 周区间条、量比、Beta、波动、距高点、距 120 日线、下次财报；市值与板块“数据接入中”）、估值与财务（数据接入中）、财报卡（倒计时、财报前是否持有、过去 8 次）、相关新闻（数据接入中）、同板块（数据接入中）

- [ ] **Step 1: 写失败测试**：`renders held stock relation`（“持有中 · 波段仓 · 12.0%”）、`renders empty stock`（`quote: null` → “暂无数据”，不一直显示加载中）、`prev and next holding navigation keeps account`、`shows 未复权 when not adjusted`、`trade row click selects marker`、`pending modules show 数据接入中`。
- [ ] **Step 2: 运行** `cd web && npx vitest run src/pages/StockPage.test.tsx` — Expected: FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** `make web-check` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 个股详情页按设计重做（Refs #65）`

### Task 9: 实盘标识补全与市场页策略摘要按账户

**Files:**
- Modify: `web/src/pages/MarketPage.tsx`（右栏“我的策略”用 `/api/signals/latest?mode=` 与该账户 overview）、`web/src/pages/MarketPage.test.tsx`

- [ ] **Step 1: 写失败测试**：`strategy summary follows account`（account=`live` 时请求带 `mode=live`，卡片标题含“实盘”）。
- [ ] **Step 2: 运行** `cd web && npx vitest run src/pages/MarketPage.test.tsx` — Expected: FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** `make web-check` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 首页我的策略按账户显示（Refs #65）`

### Task 10: 联调、对齐检查与文档

**Files:**
- Modify: `docs/PROJECT_PLAN.md`（第 7 章数据表增加 `job_attempts`、`signals.mode/detail`、`job_runs.mode`；第 10 章页面说明）

- [ ] **Step 1: 全量检查**：`make check` — Expected: 全部通过。
- [ ] **Step 2: 本地联调**：`make up && make migrate`，用 `uv run quant job daily_signal --date <最近交易日>`（`TRADING_MODE=sim`）生成一条带明细的信号；Chrome 无头截图 `/portfolio`、`/stock/SPY`、`/stock/NVDA` 在 1280 与 1600 宽度（同 #58 的命令），逐张检查同排底边对齐、列线、12px 间距、表格不换行。
- [ ] **Step 3: 更新文档并 Commit** `docs: 更新数据表与面板页面说明（Refs #65）`
