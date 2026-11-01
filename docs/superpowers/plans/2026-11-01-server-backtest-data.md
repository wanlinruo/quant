# 面板改版第 3 步：服务器回测数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让服务器上的 backtest-worker 用 Tiingo 免费档的含分红复权日线跑长周期回测（含组合策略），面板提交的回测在服务器上完成。Refs #66。

**Architecture:** 新增 Tiingo 数据提供者与额度台账；backtest-worker 空闲时按优先级、在额度内逐只补数据到独立目录 `backtest_bars`；`run_spec` 在 `tiingo` 模式下只读本地数据（不在回测中联网），并支持需要候选池的策略（成分股快照 + 候选池日线 + 历史财报日），把数据覆盖情况写进回测指标；面板显示补数进度、按策略生成提交表单、在结果上标注覆盖不足。

**Tech Stack:** Python 3.12、urllib（不新增 HTTP 依赖）、pandas、SQLAlchemy、FastAPI；React + TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 第 7.5 节；#64 结论（只用 Tiingo 免费档，第一个月补固定标的与当前标普 500 成分，第二个月补已退市历史成分，补齐前长周期回测要标注幸存者偏差，按官网额度限速，key 只在服务器 `.env`）。

## Global Constraints

- key 只来自环境变量（`SecretStr`），不进仓库、不写日志、不出现在异常信息和接口返回里；Tiingo 请求用请求头 `Authorization: Token <key>`，不放在 URL 里。
- 服务器只运行 CI 部署的镜像，不手动改文件；数据由容器自己从 Tiingo 拉取，不从 Mac 同步。
- 免费档额度（Task 1 实施时先打开 https://www.tiingo.com/about/pricing 核对并写进 `configs/base.yaml` 注释）：默认每小时 50 次、每天 1000 次、每月 500 个不同标的；额度用尽时停止拉取、下一窗口继续，不报错告警。
- 回测运行中不联网拉数据（`tiingo` 模式）：缺数据就在结果里如实标注或拒绝运行，不悄悄换数据源。
- worker 保持串行、1 CPU（设计 7.5）；补数据只在队列为空时进行，不拖慢回测。
- Mac 默认仍用 yfinance（`data.backtest_source: yfinance`），研究脚本行为不变；服务器通过环境变量切到 `tiingo`。
- 策略纯计算、回测与实盘共用同一策略类；面板只读，唯一写操作是提交回测。
- 表结构不变（本计划不需要迁移）；补数状态与额度台账存在 Parquet 数据目录的 `state/` 下。

## 我做的决定（用户审阅时可推翻）

1. **独立数据目录** `data_dir/backtest_bars`（`source="tiingo"`），不与每日任务用的主目录（老虎复权）、候选池目录（新浪）混写。
2. **补数据放在 backtest-worker 空闲时**做，每次最多拉 1 只并遵守最小间隔（3600 / 每小时上限 秒），自然摊开额度；另提供 `quant backtest-data status|sync` 命令便于人工查看和补拉。
3. **优先级**：① 固定标的（各策略 `symbols()` 的并集 + SPY、QQQ + 面板 ETF 选项）② 当前标普 500 成分 ③ 已退市的历史成分（2016 年起出现过的）。每月新标的预算 500 个，满了就等下个月。
4. **刷新**：每次拉取都取该标的从 2015-01-01 至今的全量（一次请求，复权价始终自洽）。仍在市的标的超过 30 天未刷新就重新拉；已退市的只拉一次。同一月内重复请求同一标的不额外占“不同标的”额度。
5. **回测数据截止日** = 固定标的里最早的最新日期；面板默认结束日取它，提交校验也不允许超过它。
6. **候选池覆盖**：回测用到的候选池成分里缺数据的，按年统计缺失比例写进指标（复用研究脚本里的 `missing_ratio_by_year`，挪到 `quant.engine.coverage`）；固定标的缺失则拒绝运行并说明还差哪些。
7. **历史财报日**：配置了 `QUANT_EDGAR_USER_AGENT` 时，补数据完成固定标的与当前成分后，从 SEC EDGAR 同步候选池股票的历史财报反应日到 `events/earnings_sp500.csv`（每月刷新一次）；没配置时照常回测，指标里标 `earnings: "partial"`，页面提示“历史财报日缺失，财报前离场规则在早期不生效”。
8. **组合策略提交**：面板选 `combo` 时不显示 etf_momentum 的参数，参数取服务器配置 `strategy.params`（与模拟盘一致）；`etf_momentum` 表单不变。

## Review Focus

- Tiingo 返回 404 / 空数组（代码改名、无此标的、退市代码被复用）：记为“拿不到”，不重试同一月内反复消耗额度，补数状态里能看到原因。→ Task 2 `test_unavailable_symbol_not_retried_same_month`。
- 额度在一次补数中途用完（429 或本地台账判定）：停止本轮、不丢已拉数据、下个窗口继续。→ Task 1 `test_429_marks_hour_exhausted`；Task 2 `test_stops_when_quota_exhausted`。
- 回测期间补数任务正在写同一标的的 Parquet：回测读到的是完整文件（原子替换），不会读到半个文件。→ Task 2 `test_write_is_atomic`。
- 提交的结束日晚于回测数据截止日：提交时 422 中文提示，不进队列。→ Task 4 `test_submit_end_after_data_end_is_422`。
- 回测数据目录完全为空（刚部署）：面板显示“回测数据补齐中 0/xxx”，提交组合回测时提示还缺哪些固定标的，worker 不崩溃。→ Task 3 `test_missing_fixed_symbols_fails_with_list`；Task 5 `shows backfill progress when empty`。

---

### Task 1: Tiingo 数据提供者与额度台账

**Files:**
- Create: `src/quant/data/providers/tiingo.py`、`src/quant/data/quota.py`
- Modify: `src/quant/core/settings.py`（`tiingo_api_key: SecretStr | None`、`edgar_user_agent: str | None`、`DataSettings.backtest_source: Literal["yfinance", "tiingo"] = "yfinance"`、新段 `TiingoSettings(hourly=50, daily=1000, monthly_symbols=500)`）、`configs/base.yaml`、`.env.example`
- Test: `tests/test_tiingo.py`、`tests/test_quota.py`

**Interfaces:**
- Produces:
  - `class QuotaExceeded(DataUnavailableError)`（`window: Literal["hour", "day", "month"]`）
  - `class QuotaLedger`（`quota.py`）：`__init__(path: Path, limits: TiingoSettings, now: Callable[[], datetime])`；`check(symbol: str) -> None`（超限抛 `QuotaExceeded`）；`record(symbol: str) -> None`；`mark_exhausted(window: str) -> None`；`usage() -> QuotaUsage`（`hour_used`、`day_used`、`month_symbols`、`month`）。JSON 文件原子写入；按美东时间切换小时 / 日 / 月窗口。
  - `class TiingoProvider(DataProvider)`：`source = "tiingo"`；`__init__(api_key: SecretStr, ledger: QuotaLedger, fetch: Fetch = http_fetch)`；`daily_bars(symbol, start, end)`：GET `https://api.tiingo.com/tiingo/daily/{symbol}/prices?startDate=&endDate=`，取原始 `open/high/low/close/volume` 与 `adjClose`（→ `adj_close`，拆股 + 分红），原始价格换算为“只做拆股调整”口径（见下）；空数组或 404 抛 `DataUnavailableError`；429 调 `ledger.mark_exhausted("hour")` 后抛 `QuotaExceeded`。
  - 拆股调整口径（与 `providers/base.py` 统一格式一致）：某日的累计因子 = 该日之后（不含该日）所有 `splitFactor` 的乘积；`open/high/low/close` 除以累计因子，`volume` 乘以累计因子。例：2020-08-31 `splitFactor` 4.0，则 08-28 的 `close` 499.23 → 124.81，08-31 不变。
- Consumes: `normalize_bars`、`DataUnavailableError`

- [ ] **Step 1: 核对官网额度**，把每小时 / 每日 / 每月不同标的数写进 `configs/base.yaml` 的 `tiingo:` 段注释（含核对日期）；与默认值不同则以官网为准并在台账记 Ruling。
- [ ] **Step 2: 写失败测试**：`test_maps_fields_and_split_adjusts`（构造含 `splitFactor: 4.0` 的样本：拆股前 `close` 500 → 125，`adj_close` 原样，`volume` × 4）、`test_auth_header_not_in_url`（假 fetch 记录请求：URL 不含 key，请求头 `Authorization: Token ...`）、`test_empty_or_404_raises_unavailable`、`test_429_marks_hour_exhausted`、`test_ledger_limits`（第 51 次 `check` 抛 `QuotaExceeded("hour")`；新的一小时恢复；同月第 501 个新代码抛 `"month"`，已用过的代码不抛）、`test_ledger_file_survives_restart`、`test_key_not_in_error_message`（fetch 抛出含 URL 的异常时，错误信息里没有 key）。
- [ ] **Step 3: 运行** `uv run pytest -q tests/test_tiingo.py tests/test_quota.py` — Expected: FAIL（模块不存在）。
- [ ] **Step 4: 实现**。
- [ ] **Step 5: 运行** 同上 + `uv run pytest -q tests/test_settings.py tests/test_providers.py` — Expected: PASS。
- [ ] **Step 6: Commit** `feat: Tiingo 数据提供者与额度台账（Refs #66）`

### Task 2: 回测数据补齐（backtest-worker 空闲时执行）

**Files:**
- Create: `src/quant/data/backtest_data.py`
- Modify: `src/quant/data/store.py`（`write_bars` 改为临时文件 + `os.replace` 原子替换，如尚未如此）、`src/quant/jobs/backtest_worker.py`（队列为空时调用一步补数）、`src/quant/cli.py`（`quant backtest-data status` / `quant backtest-data sync [--max N]`）
- Test: `tests/test_backtest_data.py`、`tests/test_backtest_worker.py`、`tests/test_store.py`

**Interfaces:**
- Consumes: Task 1 的 `TiingoProvider`、`QuotaLedger`、`QuotaExceeded`
- Produces:
  - `backtest_store(data_dir: Path) -> ParquetStore`（目录 `backtest_bars`）
  - `wanted_symbols(universe: Constituents | None, today: date) -> Wanted`：`Wanted(fixed: list[str], current: list[str], delisted: list[str])`；fixed = 所有注册策略默认参数下 `symbols()` 的并集 ∪ {SPY, QQQ} ∪ `api.routes.backtests.UNIVERSE_OPTIONS`（把该常量挪到 `quant.strategies` 下的公共位置，接口与本模块都从那里导入）；current = 当前成分；delisted = 2016-01-01 起出现过、当前不在的成分
  - `BackfillState`（JSON：`data_dir/state/backtest_data.json`）：每个代码 `last_fetch`、`last_date`、`status`（`ok` / `unavailable` / `invalid`）、`reason`
  - `next_symbol(wanted: Wanted, state: BackfillState, ledger: QuotaLedger, today: date) -> str | None`：按“我做的决定”第 3、4 条选下一只；本月已标 `unavailable` 的不再选
  - `backfill_step(provider, store, state, ledger, wanted, today, cal) -> StepResult`（`symbol`、`outcome`：`fetched` / `unavailable` / `invalid` / `quota` / `idle`）：拉 2015-01-01 至今，经 `validate_bars`（`max_move` 用组合策略的值）后写入；校验不过记 `invalid` 不写
  - `sync_earnings_step(...)`：当前成分与固定标的全部 `ok` 且配置了 `edgar_user_agent` 时，每月一次用 `edgar.fetch_earnings` 同步候选池股票的历史财报日到 `events/earnings_sp500.csv`（单只失败计入原因，不中断）
  - `status(wanted, state, ledger) -> BackfillStatus`：`fixed_done/total`、`current_done/total`、`delisted_done/total`、`data_end`（固定标的最新日期的最小值，没有为 None）、`unavailable` 列表、`quota`（`QuotaUsage`）、`earnings`（`complete` / `partial` / `none`）
  - worker：`process_one` 返回 None（队列空）时，若 `settings.data.backtest_source == "tiingo"` 且距上次拉取 ≥ `3600 / hourly` 秒，执行一次 `backfill_step`；任何补数异常只记日志，不影响回测

- [ ] **Step 1: 写失败测试**：`test_priority_fixed_then_current_then_delisted`、`test_monthly_budget_defers_new_symbols`（预算满时不选新代码，但已用过的代码仍可刷新）、`test_refresh_after_30_days_active_only`（已退市的不刷新）、`test_unavailable_symbol_not_retried_same_month`、`test_stops_when_quota_exhausted`（provider 抛 `QuotaExceeded` → outcome `quota`，状态不变）、`test_invalid_data_not_written`、`test_write_is_atomic`（写入过程中途抛错，原文件内容不变）、`test_status_data_end_and_progress`、`test_worker_backfills_only_when_idle`（队列有任务时不补数；空闲且到间隔时补 1 只）、`test_earnings_sync_requires_user_agent`、`test_cli_status_prints_progress`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_backtest_data.py tests/test_backtest_worker.py tests/test_store.py` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat: backtest-worker 空闲时按额度补齐 Tiingo 回测数据（Refs #66）`

### Task 3: 回测在 tiingo 模式下只读本地数据，并支持候选池策略

**Files:**
- Create: `src/quant/engine/coverage.py`（从 `scripts/research/combo.py` 挪入 `missing_ratio_by_year`，研究脚本改为导入）
- Modify: `src/quant/engine/runner.py`（`run_spec`）
- Test: `tests/test_runner.py`（新建或并入现有回测运行测试）、`tests/test_research_combo.py`

**Interfaces:**
- Consumes: Task 2 的 `backtest_store`、`status`
- Produces:
  - `run_spec(spec, settings, provider=None, refresh_data=False) -> BacktestResult`，行为：
    - `backtest_source == "yfinance"`：不变（主目录 + YFinance，自动拉取）
    - `backtest_source == "tiingo"`：用 `backtest_store` 和一个只读提供者（缺数据直接 `DataUnavailableError`），不联网；固定标的缺失或不覆盖区间 → `BacktestDataError("回测数据尚未补齐：缺 X、Y；预计 N 天后补齐")`
    - `strategy.uses_universe` 时：加载成分股快照；候选池日线 = 区间内出现过的成分中本地已有且通过校验的；事件日历 = `earnings.csv` ∪ `earnings_sp500.csv` ∪ `earnings_live.csv` + FOMC；传入 `Backtester(..., events=, universe=)`
  - `BacktestResult` 附带 `data_info: dict`，`save_result` 写入 `metrics["data"]`：`{"source", "data_end", "coverage": 区间平均覆盖率, "missing_by_year": {年: 比例}, "earnings": "complete"|"partial"|"none"}`；非候选池策略只有 `source`、`data_end`

- [ ] **Step 1: 写失败测试**（合成样本写入临时 `backtest_bars`）：`test_tiingo_mode_does_not_fetch`（传入会抛错的 provider 也不被调用）、`test_missing_fixed_symbols_fails_with_list`、`test_universe_strategy_runs_with_partial_coverage`（候选池缺 1/4 → `metrics["data"]["coverage"] == 0.75` 左右，回测完成）、`test_events_loaded_for_universe_strategy`、`test_yfinance_mode_unchanged`（现有回测测试全部通过）。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_runner.py tests/test_backtest_worker.py tests/test_research_combo.py` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** 同上 + `uv run pytest -q tests/test_backtester.py tests/test_combo.py` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 服务器回测只读 Tiingo 本地数据并支持组合策略，结果记录数据覆盖（Refs #66）`

### Task 4: 面板接口——补数进度、默认结束日、按策略提交

**Files:**
- Modify: `src/quant/api/backtests.py`（`build_submission` 按策略校验参数）、`src/quant/api/routes/backtests.py`（`/api/backtests/data-status`、`defaults`）、`src/quant/api/schemas.py`
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: Task 2 的 `status`、`wanted_symbols`
- Produces:
  - `GET /api/backtests/data-status` → `BacktestDataStatus`：`source`、`data_end`、`fixed`/`current`/`delisted`（各 `done`、`total`）、`unavailable: list[str]`、`quota`（`hour_used`、`hour_limit`、`day_used`、`day_limit`、`month_symbols`、`month_limit`）、`earnings`；`yfinance` 模式下 `source="yfinance"`，其余为空
  - `BacktestDefaults` 增加 `data_end: date | None`；`end` = `min(最近已完成交易日, data_end)`（tiingo 模式）
  - `BacktestSubmit.strategy == "combo"` 时：忽略表单里的 etf 参数，`params` 取 `settings.strategy.params`（策略名为 combo 时）或 `{}`；其余校验（日期、资金、成本）不变；`end > data_end` → 422「结束日期：不能晚于回测数据截止日 YYYY-MM-DD」

- [ ] **Step 1: 写失败测试**：`test_data_status_yfinance_mode`、`test_data_status_tiingo_progress`（临时目录写 2 只固定标的 → `fixed.done == 2`）、`test_defaults_end_clamped_to_data_end`、`test_submit_combo_uses_configured_params`、`test_submit_end_after_data_end_is_422`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_api_routes.py tests/test_api_readonly.py` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**；`make api-types`。
- [ ] **Step 4: 运行** 同上 + `uv run pytest -q tests/test_api_app.py` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 面板接口提供回测数据进度，组合策略可从面板提交（Refs #66）`

### Task 5: 回测页前端——补数进度条、按策略的表单、结果覆盖提示

**Files:**
- Modify: `web/src/pages/BacktestPage.tsx`、`web/src/pages/BacktestSubmitDrawer.tsx`、对应测试

**Interfaces:**
- Consumes: Task 4 的接口与生成类型

布局：回测记录面板上方一行状态条（12 列）：“回测数据：Tiingo · 截至 {data_end} · 固定 {a}/{b} · 当前成分 {c}/{d} · 历史成分 {e}/{f} · 本月额度 {month_symbols}/{month_limit}”，未补齐时显示黄色“补齐中”，历史成分未齐时追加“长周期结果偏乐观（缺已退市成分）”；`yfinance` 模式不显示。回测详情标题区：`metrics.data.coverage < 1` 时显示黄色“数据覆盖 {x}%”，`earnings != "complete"` 时显示“历史财报日缺失”。

- [ ] **Step 1: 写失败测试**：`shows backfill progress when empty`（`fixed.done = 0` → 显示“补齐中”）、`hides status bar in yfinance mode`、`combo form hides etf fields`（选择 combo 后不显示“回看窗口”等，提交体不含这些字段的修改）、`end date input is limited to data_end`（`max` 属性等于 `data_end`）、`shows coverage warning on run`。
- [ ] **Step 2: 运行** `cd web && npx vitest run src/pages/BacktestPage.test.tsx` — Expected: FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** `make web-check` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 回测页显示回测数据进度，组合策略表单与覆盖提示（Refs #66）`

### Task 6: 部署配置、文档与服务器验收

**Files:**
- Modify: `compose.yml`（backtest-worker 与 dashboard 增加 `QUANT_TIINGO_API_KEY: ${TIINGO_API_KEY:-}`、`QUANT_EDGAR_USER_AGENT: ${EDGAR_USER_AGENT:-}`；dashboard 只需 `QUANT_DATA__BACKTEST_SOURCE`）、`compose.server.yml`（backtest-worker 与 dashboard 设 `QUANT_DATA__BACKTEST_SOURCE: tiingo`）、`docs/PROJECT_PLAN.md`（第 7、9 章：回测数据来源）、`deploy/README.md`（`.env` 新增变量说明、`quant backtest-data status` 用法）、`.env.example`
- Test: `tests/test_settings.py`（环境变量映射）

- [ ] **Step 1: 写失败测试**：`test_env_maps_tiingo_key_and_backtest_source`（`QUANT_TIINGO_API_KEY`、`QUANT_DATA__BACKTEST_SOURCE=tiingo` 被读入；key 的 `repr` 为 `**********`）。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_settings.py` — Expected: FAIL（若 Task 1 已覆盖则记 Ruling 并跳过）。
- [ ] **Step 3: 修改 compose 与文档**；`make check` 全部通过。
- [ ] **Step 4: Commit** `chore: 服务器回测切换到 Tiingo 数据，文档说明（Refs #66）`
- [ ] **Step 5: 部署后验收**（合并到 main 并部署后，在 Mac 上经 ssh 执行，只读）：`docker compose exec -T backtest-worker quant backtest-data status` 显示补数在进行、额度计数增长、无报错日志；固定标的补齐后（约 1 小时内）在面板提交一次 etf_momentum 10 年回测并完成；当前成分补到本月额度上限后（约半天，每小时 50 次；本月 500 个标的额度用完时约剩 30 只当前成分，与已退市成分一起下月补）提交一次 10 年 combo 回测，约 3 分钟内完成，指标与 Mac 研究结果的差异在可解释范围内（数据源不同 + 缺部分成分），结果与覆盖率记入 #66。
