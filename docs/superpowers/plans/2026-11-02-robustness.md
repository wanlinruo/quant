# 回测稳健性检验（#68）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 新建回测时可勾选“同时跑稳健性检验”；backtest-worker 在父回测完成后生成一组子回测并串行执行；回测页“稳健性”标签展示参数敏感性热力图、相邻参数逐个检查、walk-forward、成本敏感性、起始时间敏感性；概览的“相邻设置”验收项用子回测结果判定。

**Spec:** 设计 5.3（稳健性标签）、7.3；PROJECT_PLAN 第 9 章（过拟合：walk-forward）；组合策略相邻设置定义见 `docs/superpowers/plans/2026-10-30-momentum-combo.md`（比例 30/70、50/50；阈值 25%、35%；只数 3、5）。

## Global Constraints

- 面板唯一写操作仍是提交回测：只在 `BacktestSubmit` 加 `robustness: bool`，父任务 `params["_robust"] = true`。子任务由 worker 插入。
- worker 保持串行，不加并发；页面显示子任务进度与排队情况。
- 表结构经 Alembic：`backtest_runs` 增加 `parent_id`（FK，`ON DELETE CASCADE`，索引）与 `variant`（JSONB，可空）。

## 决定

- **计划**（`src/quant/engine/robustness.py`，纯函数 `plan(strategy, params, start, end) -> list[Variant]`）：每个策略两个网格轴 + 若干单独参数；网格 3×3，中心格即父回测本身（不重复跑）；越界值（如 `top_k < 1`）的格子跳过。
  - combo：X = `momentum_weight` ±0.1（`swing_weight` 同步为 合计 − X），Y = `vol_threshold` ±0.05；单独参数 `momentum.top_n` ±1。
  - etf_momentum：X = `lookback` ±42（≥ 21），Y = `top_k` ±1（1..标的数）；单独 `sma_window` ±50（≥ 20）。
  - momentum_select：X = `lookback` ±42，Y = `top_n` ±1（≥ 1）；单独 `trend_ma` ±40（≥ 20）。
  - swing_pullback：X = `stop_pct` ±0.02（> 0），Y = `max_hold` ±10（≥ 5）；单独 `support_ma` ±5（≥ 5）。
  - 基准值取父回测参数中的值，没有时取参数类默认值（combo 的 swing 默认为 V2 默认值）。
  - 成本：所有费率与最低收费、滑点 ×0、×2、×3（`max_fee_pct` 上限不变）。
  - 起始时间：起点 +3、+6、+9、+12 个月（剩余区间不足 2 年的跳过）。
- **相邻参数**：网格中与中心同行或同列且相邻的 4 格 + 单独参数的 2 个。combo 用验收阈值（回撤 ≥ −30% 且夏普 ≥ 1.0）判定通过；其它策略不判定，只列出相对父回测的变化。
- **walk-forward**：用网格 9 格（含中心）的净值；训练 3 年、测试 1 个日历年，逐年滚动：每年选训练期夏普最高的格子，取其测试年收益，拼成样本外净值；与父回测同期对比（年化、夏普、最大回撤）。网格未全部完成时不计算。
- **状态**：父回测没有 `_robust` → 未运行（显示说明，不画空图）；子任务未全部结束 → 显示进度 N / M 与排队情况，已完成的部分照常展示；子任务失败的格子标“失败”。
- **列表**：回测列表不显示子任务；父任务行显示“稳健性 N / M”。

## Review Focus

1. 父回测失败：不生成子任务。
2. 父回测区间太短：起始时间敏感性全部跳过，walk-forward 显示“区间不足 4 年”。
3. 子任务部分失败：其余结果照常展示；相邻参数含失败项时不判通过（显示“不完整”）。
4. 子任务自身完成时不再生成孙任务（只看 `parent_id is None` 且 `_robust`）。
5. 删除父任务时子任务级联删除；worker 重启时 running 子任务按现有逻辑标失败。

### Task 1: 迁移 + `engine/robustness.py` 计划（`tests/test_robustness.py`）
### Task 2: worker 父任务完成后生成子任务（`tests/test_backtest_worker.py`）
### Task 3: 提交与列表（`BacktestSubmit.robustness`、列表隐藏子任务、进度）
### Task 4: 接口 `GET /api/backtests/{id}/robustness`（`api/robustness_view.py`，含 walk-forward）；概览“相邻设置”用子回测
### Task 5: 前端稳健性标签与抽屉勾选
### Task 6: `make check`、终审、部署、服务器跑一次 etf_momentum 稳健性检验核对，记入 #68
