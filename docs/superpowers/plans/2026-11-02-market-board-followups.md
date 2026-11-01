# 首页市场看板后续修复（#79）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 处理 #79 中有明确修法的问题：数据正确性、Finnhub / FRED 请求、财经日历、运维与文档、个股页显示、模块分层。

**Architecture:** 都是对前三批代码的局部修改，不加新数据源、不改接口形状（只加可选字段）。每项先写失败测试。

**Tech Stack:** Python 3.12、FastAPI、pandas、React 19 + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md`（5.1、5.4、7.6）；前三批计划 `docs/superpowers/plans/2026-11-01-market-board-batch{1,2,3}.md`；问题清单 #79。

## Global Constraints

- 面板只读；key 不进仓库、日志、异常；免费数据不提交；业务时间美东。
- 不改已有接口字段的含义；新增字段一律可选。

## 本轮不做（需要先定方案，留在 #79）

- 任务局部失败在面板上可见（要改 job_runs 与系统页，属于运行框架的设计）
- 快照 JSON 中热力图数据的重复与接口缓存（体积与缓存策略）
- 板块 ETF 行与个股行截止日期不一致的提示
- 前端细节：错误与待接入区分、宽度缺“平”、指数日线区间切换、热力图标签阈值；`_signals` 的 dropna 对齐

## Review Focus

1. 历史不足一年时，今日要点不出现“52 周”字样，宽度表的 52 周新高 / 新低显示“—”，而不是 0。——Task 1 测试
2. 某只成分股当天没有数据：热力图 1日 为空（灰色），不显示几天前的涨跌。——Task 1 测试
3. 个股新闻过滤后为空：页面写“近 7 天无相关新闻”，不是“数据接入中”。——Task 2、Task 5 测试
4. 部署时数据任务正在运行：scheduler 等任务完成再退出（`stop_grace_period`）。——Task 4 检查 compose 配置
5. 清理不在覆盖范围的个股文件时，GICS 表读不到不得删除任何文件。——Task 6 测试

---

### Task 1: 市场数据正确性（`market/job.py`、`market/board.py`、`data/market_store.py`）

- [ ] 失败测试（`tests/test_market_job.py`、`tests/test_market_board.py`）：
  - `test_past_date_rerun_still_backfills`：先跑 10-30，再以 10-29 运行，补历史照常进行（拆股核对进度不早于运行日期即视为已追上：`>= today`）
  - `test_today_low_coverage_refetched`：当日文件只有 50% 成分 → 再次运行时重取当日；覆盖率 ≥ 95% 不重取
  - `test_empty_day_not_requested_again`：某个交易日返回空表 → 记入 `market/empty_days.json`，之后补历史跳过；当日（today）不记
  - `test_highlights_without_year_history`：历史 61 天 → 要点不含“52 周”；`breadth.new_highs.value is None`
  - `test_heatmap_1d_none_when_stale`：某股最后一个收盘早于快照日 → 热力图 `1D` 的 `ret is None`
  - `test_movers_keep_signal_rows_beyond_cap`：200 只涨跌 ≥2%（无信号）+ 30 只有信号且涨跌 <2% → 结果含全部 30 只有信号的；无信号的最多 150 只
- [ ] 实现：
  - 覆盖率 = 当日文件中成分股数 / 成分股数；低于 0.95 时重取当日（每次运行最多一次）
  - 空日：`MarketStore.mark_empty(day)`、`empty_days() -> set[date]`
  - 宽度：没有任何成分股满足 252 日历史时，新高 / 新低为 None；要点里 SPY 收盘少于 252 日时不写“距 52 周高点”
  - 热力图：`PERIODS` 各期与 YTD 只在该股最后收盘日等于快照日时计算，否则 None
  - 异动：先按 |涨跌| 取前 150（`MOVER_LIMIT`），再并入其余有信号的（最多再加 `SIGNAL_EXTRA = 100`），持仓照旧全部并入；最终按 |涨跌| 排序
- [ ] 提交：`fix: 市场数据补跑、覆盖率重取、短历史要点、热力图过期收益、异动信号截断（Refs #79）`

### Task 2: 新闻与请求（`market/news.py`、`market/extras_job.py`、`market/stock_job.py`）

- [ ] 失败测试：
  - `test_company_news_end_is_next_day`（extras）：公司新闻请求的结束日 = 运行日 + 1
  - `test_fred_stops_after_network_failure`（extras）：第一个 FRED 序列抛非认证的 `DataUnavailableError` 后，本次不再请求 FRED（宏观与实际值），摘要写一次
  - `test_holdings_feed_only_relevant`（news）：持仓公司新闻只收标题提到代码或公司简称的（复用 `_mentions`）
  - `test_stock_news_only_relevant`（stock_job）：“5 Best High Growth Stocks…”被过滤，“Apple beats…”保留；没有相关新闻时 `news == []` 且 `news_updated` 有值
  - `test_stock_metrics_before_news_and_stage_in_summary`：指标先于新闻请求；新闻阶段限流时摘要含“停在新闻”，指标已写入
- [ ] 实现：`build_feed` 与 `stock_job._items` 需要公司名——`build_feed` 新增参数 `names: Mapping[str, str | None]`；`stock_job` 从 GICS 表取名称（持仓不在表里时只按代码匹配）
- [ ] 提交：`fix: 个股与持仓新闻只保留相关标题；FRED 故障即停；财务指标先取（Refs #79）`

### Task 3: 财经日历（`market/calendar.py`）

- [ ] 失败测试：`test_fomc_not_duplicated_with_ff`（FF 当天有 “Federal Funds Rate” 时不再生成“FOMC 利率决议”，FF 那一条标题改为“FOMC 利率决议”、重要性 3）；`test_dmh_is_intraday`（`hour="dmh"` → `session == "盘中"`、`date_only is False`）
- [ ] 提交：`fix: 财经日历 FOMC 去重、盘中财报（Refs #79）`

### Task 4: 运维与文档

- [ ] `compose.yml` scheduler 加 `stop_grace_period: 20m`（stock_data 约 14 分钟）；`deploy/server_init.md` 注明：部署会等正在运行的任务结束（最多 20 分钟）；21:00–21:30 ET 不要手动运行 Finnhub 任务（与调度叠加会超额）
- [ ] `.env.example` 补 `FRED_API_KEY`、`FINNHUB_API_KEY` 的注释行（同 Massive 写法）
- [ ] 检查：`docker compose config` 中 scheduler 的 `stop_grace_period` 为 20m
- [ ] 提交：`chore: scheduler 停止宽限 20 分钟；文档与 .env.example（Refs #79）`

### Task 5: 个股页与首页显示

- [ ] 失败测试（`StockPage.test.tsx`、`MarketPage.test.tsx`）：
  - 个股新闻面板与新闻标签显示“更新于 MM-DD HH:MM ET”（取 `news_updated`）
  - 已取过但无相关新闻：“近 7 天无相关新闻”
  - 首页板块表现右上角注明“价格涨跌，不含分红”；热力图空状态写“市值每天补 60 只，约 9 个交易日补齐”
- [ ] 提交：`fix: 个股新闻更新时间与无相关新闻；首页口径说明（Refs #79）`

### Task 6: 分层与清理

- [ ] `period_returns` 移到 `src/quant/market/periods.py`（`api/analytics.py` 从那里导入并保持原名导出）；`METRICS` / `METRIC_KEYS` 移到 `src/quant/market/stock_metrics.py`；`SECTOR_NAME` 移到 `data/market_store.py`。`quant.market.board` 不再导入 `quant.api`，`quant.api.stock_extras` 不再导入 `quant.market.stock_job`——加测试 `test_layering`（用 `ast` 检查这两条导入）
- [ ] `stock_job`：GICS 表能读到时，删除 `stocks/` 下不在覆盖范围的文件；读不到时一个都不删（`test_stale_stock_files_removed`、`test_no_cleanup_without_gics`）
- [ ] `test_extras_rejects_bad_symbol` 改为直接调用 `stock_extras(data_dir, "../x")` 等，断言 covered=False 且没有读数据目录外的文件
- [ ] 提交：`refactor: 市场模块分层、清理停用个股文件（Refs #79）`

### Task 7: 收尾

- [ ] `make check` 全部通过（带 `QUANT_TEST_DATABASE_URL`）
- [ ] 部署后：确认 scheduler 的停止宽限生效（`docker inspect` 的 StopTimeout = 1200）；结果与本轮未做的 4 项写入 #79
