# 回测页 B 批（逐笔交易与每日持仓存储、交易明细与持仓变化标签）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回测时记录逐笔交易（含子仓与平仓原因）与每日持仓 / 现金 / 择时系数，上线“交易明细”“持仓变化”两个标签，并补齐统计条的胜率、盈亏比、交易笔数，以及收益分析的子仓贡献、回撤与风险的择时效果。

**Architecture:** 策略基类新增 `run_attributed(ctx) -> (weights, Attribution)`：默认单一子仓、没有原因；combo 由现有唯一计算路径 `breakdown` 给出两个子仓的权重、择时系数与当日平仓原因（波段规则的退出判断改为返回原因，决策不变）。回测器改用它并记录每日实际持仓（股数、市值）与成交；纯函数 `engine/trades.py::build_ledger` 按设计 7.1 的口径切分交易、拆分子仓。结果写入三张新表（Alembic 迁移）。实盘与模拟盘仍走 `run` / `run_explained`，行为不变。

**Tech Stack:** Python 3.12、pandas、SQLAlchemy + Alembic、FastAPI、React 19 + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 5.3（交易明细、持仓变化）、7.1；#67；A 批计划 `docs/superpowers/plans/2026-11-02-backtest-page-a.md`。

## Global Constraints

- 策略保持纯计算、无 I/O；回测与实盘共用同一策略代码，**不能**让两边走两套计算。对已有策略，`run_attributed` 返回的权重必须与 `run` 完全一致（测试逐日比对）。
- 表结构只能通过 Alembic 迁移修改；面板只读。
- 设计 7.1 的口径（用户已决定）：同一 (symbol, 子仓) 从建仓到清仓算一笔；中途加减仓（含择时减仓、再平衡）是这笔交易的调整；只减仓不清仓不产生平仓原因；同一只股票同时在两个子仓时按子仓分别记录，盈亏按各子仓的权重比例拆分。
- 存储量参考：10 年 combo 约 1,300 笔交易、约 2.5 万行每日持仓。

## 决定

- **Attribution**：`@dataclass(frozen=True) Attribution(sleeves: dict[str, dict[str, float]], timing_factor: float | None, exits: dict[str, str])`。`sleeves` 为各子仓对组合的权重贡献（已乘子仓比例与择时系数），各子仓之和等于 `weights`。默认实现：`{"main": weights}`、`None`、`{}`。
- **平仓原因文案**：波段 —— “持有满 N 天”“止损 -X%”“FOMC 前离场”“跌破支撑线”“跌破 N 日线（已获利）”“财报前离场”“反向标的转回”；动量 —— 调仓日跌出前 `top_n`：“动量排名跌出前 N”；其它（目标变为 0 但策略没给原因）：“目标调整”；回测结束仍持有：平仓字段为空。
- **交易切分**输入：每日收盘持仓 `{day: {symbol: (qty, market_value)}}`、成交列表、每个信号日的 Attribution。子仓份额取**最近一个信号日**该股在各子仓的权重占比（信号日 t 决定、t+1 开盘成交，t+1 收盘起按新份额）。一笔交易：(symbol, 子仓) 份额由 0 变正的那天开仓，再变回 0 的那天平仓；开 / 平仓价 = 当天该股买入 / 卖出成交均价（没有成交时用当日收盘价）；数量 = 开仓当天股数 × 份额；盈亏 = 持有期每日 `(市值_t − 市值_{t−1} − 当日净买入额)` × 前一日份额之和（含成本，因为成交额含佣金滑点）；收益率 = 盈亏 ÷ 期间最大投入额（开仓市值与加仓后市值的最大值）。
- **新表**：`backtest_trades(run_id, seq, symbol, sleeve, open_date, open_price, close_date, close_price, qty, ret, pnl, exit_reason, adjustments)`（adjustments 为 JSON 列表：日期、股数变化）；`backtest_positions(run_id, date, symbol, sleeve, weight)`，主键 (run_id, date, symbol, sleeve)；`backtest_daily(run_id, date, cash_weight, timing_factor)`。都随 `backtest_runs` 级联删除。
- **统计条**：胜率 = 盈亏 > 0 的已平仓笔数 / 已平仓笔数；盈亏比 = 盈利总额 / 亏损总额绝对值；交易笔数 = 全部笔数（含未平仓）。旧回测（没有交易记录）三项仍为空，标签页显示“此回测运行于记录功能上线之前，重新运行即可查看”。
- **子仓贡献**：每日各子仓盈亏累加得到累计贡献曲线（从 backtest_trades 无法逐日还原，改为回测时把每日子仓盈亏写入 `backtest_daily` 的 `sleeve_pnl` JSON 列）。**择时效果**：系数 < 1 的连续时段，列出起止、天数、该时段策略收益与“系数为 1 时的估算收益”（期间收益 ÷ 平均系数 × 1，近似）及差值。
- 新接口：`GET /api/backtests/{id}/trades`（汇总卡、逐笔列表、按平仓原因、按股票、持有天数 × 收益散点）、`GET /api/backtests/{id}/positions`（每日子仓 / 现金占比与择时系数、指定日期持仓 `?date=`、持有时间最长的股票）。

## Review Focus

1. 已有策略（etf_momentum、swing_pullback、combo）在 10 年回测上，改造后的净值与改造前**逐日完全一致**（不能因为换了调用入口改变任何决策）。——Task 2 测试（用固定合成数据跑两遍对比）
2. 同一只股票同时在两个子仓、其中一个子仓先清仓：只关掉那个子仓的一笔，另一笔继续，盈亏按份额拆分、两笔之和等于该股总盈亏。——Task 3 测试
3. 回测结束仍持有：交易的平仓字段为空、计入交易笔数、不计入胜率。——Task 3 测试
4. 旧回测（没有新表数据）：接口返回空列表并说明，前端不报错。——Task 5 测试
5. 迁移可升级、可降级，降级后旧数据不受影响。——Task 4 测试

---

### Task 1: 策略归因输出

**Files:** Modify `src/quant/strategies/base.py`、`swing_pullback.py`、`combo.py`；Test `tests/test_strategy_attribution.py`

- [ ] 失败测试：`Attribution` 默认实现（etf_momentum：单子仓 = run 的权重、exits 空）；swing `_should_exit` 各分支返回对应原因文案（构造最小 `_View` / 用已有测试夹具）；combo 在合成数据上 `run_attributed` 的权重与 `run` 完全一致、各子仓之和 = 权重、调仓日被替换的动量股原因为“动量排名跌出前 N”
- [ ] 实现：`_should_exit` → `_exit_reason(...) -> str | None`（`simulate` 里 `if reason := ...`）；`simulate(..., exits: dict[str, str] | None = None)` 记录 `as_of` 当天的退出；`SwingPullback.attributed(ctx)`；`ComboBreakdown` 增 `swing_exits`，`breakdown` 用它（同一次计算）；`Combo.run_attributed` 由 breakdown 组装（不调用 `_rank_asof`）
- [ ] 提交：`feat: 策略归因输出——子仓权重、择时系数与平仓原因（Refs #67）`

### Task 2: 回测器记录归因与每日持仓

**Files:** Modify `src/quant/engine/backtester.py`；Test `tests/test_backtester.py`（追加）

- [ ] 失败测试：`BacktestResult` 新增 `attribution: dict[date, Attribution]`、`holdings: dict[date, dict[str, tuple[float, float]]]`（股数、市值）；对 etf_momentum 与 combo（已有测试用的合成数据）改造前后 `equity` 逐日完全相等（先在测试里固定一份改造前的结果：用现有 `run` 路径的旧实现作对照不可行时，比较 `run_attributed` 权重与 `run` 权重逐日相等即可）
- [ ] 实现：循环里用 `run_attributed`（权重取第一个返回值，其余逻辑不变）
- [ ] 提交：`feat: 回测记录每日归因与持仓（Refs #67）`

### Task 3: 交易切分（纯函数）

**Files:** Create `src/quant/engine/ledger.py`；Test `tests/test_ledger.py`

**Interfaces:** `build_ledger(holdings, fills, attribution, equity) -> Ledger(trades: list[TradeRow], positions: list[(date, symbol, sleeve, weight)], daily: list[(date, cash_weight, timing_factor, sleeve_pnl: dict[str, float])])`

- [ ] 失败测试：单子仓建仓 → 加仓 → 减仓 → 清仓 = 1 笔（2 次调整）、平仓原因取平仓前信号日的 exits；两子仓同股、一个先清仓 → 2 笔、盈亏之和 = 该股总盈亏；结束仍持有 → close 为 None；没有成交的日子用收盘价；每日子仓权重之和 + 现金 = 1
- [ ] 提交：`feat: 回测交易切分与子仓拆分（Refs #67）`

### Task 4: 存储（迁移、模型、写入）

**Files:** Create `alembic/versions/<rev>_backtest_trades.py`；Modify `src/quant/db/models.py`、`src/quant/db/backtests.py`、`src/quant/engine/runner.py`（`save_result` 写入新表）；Test `tests/test_db.py`（追加）、`tests/test_migrations.py`（若有）

- [ ] 失败测试：升级后三张表存在；`complete_run` 写入并可重复覆盖；删除回测级联删除；降级再升级成功
- [ ] 提交：`feat: 回测逐笔交易与每日持仓存储（Refs #67）`

### Task 5: 接口

**Files:** Modify `src/quant/api/backtest_view.py`、`routes/backtests.py`、`schemas.py`；Test `tests/test_api_backtest_analysis.py`（追加）

- [ ] 失败测试：`/trades` 汇总（笔数、胜率、盈亏比、平均持有天数、最大盈利 / 亏损）、列表、按原因、按股票；`/positions` 每日子仓 / 现金、`?date=` 当日持仓、最长持有；分析接口统计条后三项有值、`returns.sleeves`、`risk.timing`；旧回测返回空并带 `note`
- [ ] 提交：`feat: 回测交易明细与持仓变化接口（Refs #67）`

### Task 6: 前端

**Files:** Create `web/src/pages/backtest/TradesTab.tsx`、`PositionsTab.tsx`；Modify `AnalysisTabs.tsx`（子仓贡献、择时效果）、`BacktestPage.tsx`；Test `BacktestPage.test.tsx`

- 交易明细：6 个汇总卡；逐笔表（开平仓日期与价格、天数、收益、盈亏、平仓原因；按子仓 / 盈亏筛选；导出 CSV；点一行在主图标出开平仓——主图加 markers）；持有天数 × 收益散点（SVG）；按平仓原因统计；按股票汇总。表高与右侧三块之和一致（内部滚动）
- 持仓变化：子仓 / 现金占比堆叠面积图（标出择时减仓期）、选日期看当天持仓、持有时间最长的股票
- [ ] 失败测试、实现、`make web-check`、提交：`feat: 回测页交易明细与持仓变化（Refs #67）`

### Task 7: 联调与验收

- [ ] `make check`；本地 etf_momentum 回测重新运行后检查两个标签；1280 / 1600 截图
- [ ] 部署后在服务器重新提交一次回测（etf_momentum；候选池补齐时再跑 combo），核对交易笔数与 `fills` 一致性、子仓贡献之和等于总收益，记入 #67
