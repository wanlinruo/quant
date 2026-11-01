# 回测页 C 批（对比模式、按策略生成的回测表单、代码版本与运行日志）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成设计 5.3 剩下的部分：勾选 2–5 个回测时的对比视图；新建回测抽屉按所选策略自动生成参数表单；回测记录代码版本与运行日志并在“参数与日志”标签显示。

**Architecture:** 新只读接口 `GET /api/backtests/compare?ids=` 用 A 批的分析纯函数在共同区间上计算；新只读接口 `GET /api/backtests/param-schema` 由各策略参数数据类自省得到字段、类型、默认值（服务器配置的策略参数覆盖默认值）；`BacktestSubmit` 增加可选 `params`，非表单策略提交时用它（经策略自身的参数校验），没给时仍取服务器配置。回测运行时把代码版本（镜像构建时注入的 `QUANT_GIT_SHA`，本地取 `git rev-parse`）与运行日志（最多 200 行）写入 `metrics`。

**Tech Stack:** Python 3.12、pandas、FastAPI、React 19 + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 5.3（对比模式、新建回测、参数与日志）；#67；A / B 批计划 `docs/superpowers/plans/2026-11-02-backtest-page-{a,b}.md`。

## Global Constraints

- 面板唯一写操作仍是提交回测（`POST /api/backtests`）；新接口只有 GET。
- 策略参数只能经策略自己的 `from_mapping` / `warmup_days` 校验后入库；未知参数拒绝（422），不静默忽略。
- 不改表结构（代码版本与日志放 `backtest_runs.metrics` JSON）。

## 决定

- **对比**：共同区间 = 各回测净值起点的最大值到终点的最小值；共同区间少于 20 个交易日时返回说明、不计算。净值在共同区间起点归一化到 1。指标（总收益、年化、夏普、索提诺、最大回撤、卡玛、年波动、胜率、交易笔数）按共同区间重算（胜率与交易笔数取各自全区间的交易记录，标注“全区间”）；每项标出最优（回撤取最大值即最浅、波动取最小）。参数差异：所有回测参数键的并集，值不全相同的行标记。月收益相关性：共同区间内月收益的皮尔逊相关矩阵。
- **参数表单**：`param-schema` 返回 `{strategy: [{key, group, type: "int"|"float"|"str"|"bool"|"list", default}]}`；combo 的 `momentum` / `swing` 展开为分组字段（swing 的默认值为 V2 默认值）；服务器配置中该策略的参数覆盖默认值（这样“新建回测”默认与模拟盘一致）。etf_momentum 仍用现有表单。前端只提交与默认值不同的字段（嵌套组提交整组中改过的键）。
- **代码版本**：Dockerfile 增加 `ARG GIT_SHA` → `ENV QUANT_GIT_SHA`，部署工作流构建时传入；运行时 `QUANT_GIT_SHA` 优先，没有时取 `git rev-parse --short HEAD`，都没有为 `None`。写入 `metrics["code_version"]`。
- **运行日志**：`run_spec` 期间挂一个只收 `quant` 日志器 INFO 及以上的处理器，最多 200 行（超过时保留前 100 与后 100，中间写“… 省略 N 行”），写入 `metrics["log"]`（列表）；失败的回测已有 `error`。

## Review Focus

1. 对比的回测区间不重叠：返回说明，前端提示“没有共同区间”，不报错。——Task 1 测试
2. 对比中有未完成 / 失败的回测：跳过并在说明里列出编号。——Task 1 测试
3. 提交带未知参数或非法值（如 `top_n: -1`）：422 且列出原因，不入库。——Task 3 测试
4. 表单只改了一个嵌套字段（如 swing.stop_pct）：入库的参数只含这一项覆盖，其余仍随策略默认 / 服务器配置。——Task 3 测试
5. 日志很多（> 200 行）：保留首尾、注明省略，`metrics` 不膨胀。——Task 4 测试

---

### Task 1: 对比接口

**Files:** Modify `src/quant/api/backtest_analysis.py`（`common_range`、`monthly_corr`）、`src/quant/api/backtest_view.py`（`compare_view`）、`routes/backtests.py`、`schemas.py`；Test `tests/test_backtest_analysis.py`、`tests/test_api_backtest_analysis.py`

- [ ] 失败测试：两个回测（2016–2026 与 2019–2024）→ 共同区间 2019–2024、序列从 1 起、指标按共同区间、最优标记正确、参数差异只标不同的键、相关矩阵对角线为 1；不重叠 → `note`；含 running 的 → 跳过并列出
- [ ] 实现、`make api-types`、提交：`feat: 回测对比接口（Refs #67）`

### Task 2: 对比视图

**Files:** Create `web/src/pages/backtest/CompareView.tsx`；Modify `BacktestPage.tsx`；Test `BacktestPage.test.tsx`

勾选 ≥ 2 个时右侧显示：标题（共同区间、跳过的回测）、净值叠加（共同区间、起点 = 1）、回撤叠加、指标并排表（最优标绿）、参数差异表（不同的标黄）、月收益相关性矩阵（颜色深浅）。

- [ ] 失败测试、实现、`make web-check`、提交：`feat: 回测对比视图（Refs #67）`

### Task 3: 按策略生成参数表单

**Files:** Modify `src/quant/api/schemas.py`（`BacktestSubmit.params: dict | None = None`、`ParamField`）、`src/quant/api/backtests.py`（`param_schema`、`build_submission` 用 `req.params`）、`routes/backtests.py`；`web/src/pages/BacktestSubmitDrawer.tsx`；Test `tests/test_api_routes.py`（追加）、`BacktestPage.test.tsx`

- [ ] 失败测试：schema 含 combo 的 `momentum_weight`、`swing.stop_pct`（默认来自 V2）、`momentum.top_n`；配置的参数覆盖默认；提交 combo 带 `params={"swing": {"stop_pct": 0.05}}` → 入库的 params 只含这一覆盖；未知键 → 422；前端选 combo 时显示分组字段，改一项后提交体只含该项
- [ ] 实现、提交：`feat: 新建回测表单按策略生成参数（Refs #67）`

### Task 4: 代码版本与运行日志

**Files:** Modify `Dockerfile`、`.github/workflows/deploy.yml`、`src/quant/engine/runner.py`；`web/src/pages/backtest/AnalysisTabs.tsx`（参数与日志）；`src/quant/api/backtest_view.py`（repro 增 `code_version`、`log`）；Test `tests/test_runner.py`（追加）、`BacktestPage.test.tsx`

- [ ] 失败测试：`QUANT_GIT_SHA=abc123` 时 metrics 记 `abc123`；回测期间 `quant.*` 日志进入 `metrics["log"]`；超过 200 行时保留首尾并注明省略；前端显示代码版本与日志（只看警告的开关）
- [ ] 实现、提交：`feat: 回测记录代码版本与运行日志（Refs #67）`

### Task 5: 联调与验收

- [ ] `make check`；本地两次回测对比截图（1280 / 1600）
- [ ] 部署后服务器提交一次 combo 自定义参数回测（若候选池数据已足够）或 etf_momentum 改参回测，核对参数入库、代码版本为部署的 SHA、对比视图；结果记入 #67，#67 完成后关闭
