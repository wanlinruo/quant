# 系统页 A 批（现有数据能做的部分）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 系统页按设计 5.5 重做：总状态横幅、6 个状态卡、定时任务 30 个交易日网格与今天时间线、数据源与额度、风控限额、接下来的交易日、告警与事件、回测队列。资源卡、服务容器、资源趋势与磁盘预测等 #63 确认后再做（B 批），本批显示“待确认采集方式（#63）”。

**Architecture:** 新只读接口 `GET /api/system/overview?mode=`，由 `src/quant/api/system_view.py` 组装：任务记录（`job_runs` / `job_attempts`）、调度计划（`jobs.scheduler.day_plan`）、日历、熔断开关、订单、回测队列、各数据文件的生成时间与额度台账。`GET /api/system/job-runs?mode=&job=&date=` 返回某次任务的每次尝试。前端 `SystemPage` 按 12 列网格重排。

**Tech Stack:** Python 3.12、SQLAlchemy、FastAPI、React 19 + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 5.5、6；#71；#63（待确认，不在本批）。

## Global Constraints

- 面板只读：新接口只有 GET；不访问 Docker、不持有老虎凭证、不连外网。
- 业务时间美东；交易日、半日市以 `XNYS` 为准。
- 12 列网格、间距 12px、同排等高、表格不换行；1280 / 1600 检查。

## 决定

- **总状态**：出错（红）= 今天（最近一个交易日）有失败的任务（附处理命令 `quant job <任务> --date <日期> --rerun-failed`，在 scheduler 容器内执行）；需要注意（黄）= 熔断开关关闭（paper / live 模式）、到了计划时间仍未运行的任务、行情数据有问题的标的、首页 / 个股数据文件超过 2 个交易日未更新、回测有失败（近 7 天）；否则正常（绿）。每条原因一行。
- **状态卡**：交易模式；熔断开关（含最近修改时间）；券商连接（最近一次成功的 `daily_execute` / `daily_report` 时间，sim 模式显示“本地模拟”）；持仓对账（最近一次 `daily_execute` 的结果：成功 = 已对账、失败 = 错误第一行）；今日任务（已成功 / 今天计划的任务数）；数据完整度（行情数据状态中 ok 的标的数 / 总数）。
- **任务网格**：最近 30 个交易日 × `JOBS`；每格状态：`success`、`retry_success`（成功且尝试次数 > 1）、`failed`、`running`、`skipped`（成功但摘要以“跳过”开头或含“未配置”）、`missing`（应运行却没有记录且已过计划时间）、`pending`（还没到时间 / 非今天的未来）。每行汇总：成功率（成功或重试后成功 / 有记录的格）、平均耗时（秒）。
- **今天时间线**：`day_plan(最近交易日)` 的每个任务：计划时间（美东）、状态、耗时、错误第一行。
- **数据源与额度**：行情日线（新浪 / 老虎，数据状态）、回测数据（Tiingo：补齐进度与本月额度）、首页市场（`market/snapshot.json` 生成时间与覆盖率）、宏观（`macro.json`）、新闻（`news.json`）、财经日历（`calendar.json`）、个股（`market/stocks` 文件数、有财务指标的数量、最旧 / 最新更新时间）。每项状态：正常 / 过期（超过 2 个交易日）/ 待接入（文件不存在）。
- **风控限额**：`RiskSettings` 的上限，配合今日用量：当日换手（当日成交额 ÷ 最新净值）、单只最大权重（最新持仓快照）、总仓位、今日最大单笔金额；近 30 天被拒订单数（`orders.status = REJECTED`）；标的范围（白名单 / sp500）。
- **接下来的交易日**：从今天起 10 个交易日：日期、星期、标记（月末调仓日 = 当月最后一个交易日；FOMC；半日市（提前收盘时间））；区间内的休市日（工作日但非交易日）也列出。
- **告警与事件**（近 30 天，最多 30 条，时间倒序）：任务失败的尝试、熔断开关修改、回测失败、被拒订单。
- **回测队列**：排队 / 运行中 / 完成 / 失败数；运行中的那个（编号、策略、开始时间、已运行时长）；最近一次完成的时间。

## Review Focus

1. 没有数据库（本地 `make up` 之外）：接口返回 pending 级别与说明，不 500。——Task 1 测试
2. 半日市当天：计划时间按提前收盘前移（与 scheduler 一致）。——Task 1 测试
3. 今天是非交易日（周末）：网格与时间线以最近一个交易日为“今天”，不把周末算作缺失。——Task 1 测试
4. 某任务从未运行过（新任务）：只在其上线之后的日子标记缺失，之前显示“—”（不可用）——以该任务第一次出现的日期为界。——Task 1 测试
5. mode 切换（模拟盘 / 实盘）：网格、时间线、风控用量只看该账户的数据。——Task 1 测试

---

### Task 1: 系统概览接口

**Files:** Create `src/quant/api/system_view.py`；Modify `src/quant/api/routes/system.py`、`schemas.py`；Test `tests/test_api_system.py`

- [ ] 失败测试（测试库，构造 job_runs / job_attempts / orders / 回测与数据文件）：横幅三种级别与处理命令；状态卡；网格的 7 种状态与汇总；时间线的计划时间（含半日市）；数据源各项状态；风控用量；接下来的交易日（含 FOMC、半日市、休市）；事件合并排序；回测队列；Review Focus 1–5
- [ ] 实现、`make api-types`、提交：`feat: 系统页概览接口（Refs #71）`

### Task 2: 任务运行详情接口

- [ ] `GET /api/system/job-runs?mode=&job=&date=` → 该次任务的每次尝试（起止、耗时、结果、错误前 20 行）；不存在返回空列表
- [ ] 提交：`feat: 系统页任务运行详情接口（Refs #71）`

### Task 3: 系统页前端

**Files:** Modify `web/src/pages/SystemPage.tsx`；Create `web/src/pages/system/*.tsx`；Test `web/src/pages/SystemPage.test.tsx`

按设计 5.5 的 7 行布局（资源卡、服务容器、资源趋势、磁盘预测显示“待确认采集方式（#63）”）；网格格子可点，打开运行详情。

- [ ] 失败测试、实现、`make web-check`、提交：`feat: 系统页按设计重做（Refs #71）`

### Task 4: 联调与验收

- [ ] `make check`；本地截图 1280 / 1600
- [ ] 部署后核对服务器系统页（周一晚的每日任务跑完后网格应出现首个完整交易日），记入 #71
