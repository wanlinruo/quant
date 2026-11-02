# 首页全市场盘中刷新（Finnhub）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 首页现有区块（行情条、热力图、板块、风格、市场宽度、异动个股、指数日线）在常规交易时段约每 10 分钟用盘中价格刷新；收盘后、夜间日线快照生成前，仍显示今天的盘中结果；夜间任务完成后换成官方日线。不另加面板。

**Spec:** #93（用户 2026-11-02：要全市场在盘中更新，不要单独面板；数据源选 Finnhub 免费档；单独面板已撤掉，PR #95）。

## Global Constraints

- 只供查看：交易继续只用日线；`quotes` 服务不加载老虎配置、不导入券商与执行器（已有测试）。
- Finnhub 免费档 60 次/分钟（每次 1 只，间隔 ≥ 1.1 秒）：约 540 只一轮约 10 分钟；只在常规时段采集（免费档只覆盖常规时段）。
- 面板只读；首页快照仍是“预计算文件 + 只读接口”的模式。

## 决定

- **标的**：ETF_SET（22 只）→ 持仓 → 标普 500 成分股 → `quotes.watchlist`，去重；不再截断为 50 只（上限 700）。
- **当日 K 线**：Finnhub `/quote` 的 o / h / l / c（`quote_time` 在今天开盘后的才算）；没有成交量，记为空值。
- **快照**：`build_snapshot(..., today=当日 K 线)`：当日晚于已存日线时，作为最后一行拼到各面板后重算；依赖成交量的指标（市场宽度的上涨成交量占比、异动个股的量比与放量信号、指数日线最后一根的成交量）为空。快照新增 `intraday: bool`、`intraday_time`（本轮最新报价时间）。
- **写文件**：每轮结束写 `market/snapshot_intraday.json`（原子写入）；quotes 容器以读写方式挂载数据卷。
- **接口**：`/api/market/overview` 在盘中快照的 `as_of` 晚于日线快照、且等于今天（美东）时返回盘中快照，否则返回日线快照。
- **前端**：首页数据时间一行：盘中显示“盘中 · 截至 HH:MM ET · 约每 10 分钟更新（Finnhub）· 成交量相关指标收盘后更新”，否则保持原样。

## Review Focus

1. 当日 K 线只覆盖部分标的（限流中断）：缺的标的当日为空，按原有“样本不足为 None”处理，不报错。
2. 夜间日线快照生成后（as_of 相同），优先日线快照。
3. 次日盘前：盘中快照是昨天的，不使用。
4. 成交量为空不导致前端图表报错（指数日线最后一根）。
5. 隔离不变：导入 `quant.jobs.quotes` 后没有 tigeropen / broker / engine。

### Task 1: `build_snapshot` 支持当日 K 线（`market/board.py`，`tests/test_market_board.py`）
### Task 2: quotes 服务全市场采集并写盘中快照（`jobs/quotes.py`、compose 挂载）
### Task 3: 首页接口选择快照 + 前端时间行
### Task 4: `make check`、终审、部署（避开 09:30–10:30、16:30–21:30 ET）、盘中实测，记入 #93
