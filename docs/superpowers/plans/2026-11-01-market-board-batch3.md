# 首页市场看板第 3 批（个股页：估值与财务、新闻、同板块）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 个股页填上“数据接入中”的部分：估值与财务、相关新闻与新闻标签、同板块、市值 / 板块、区间表现里的板块 ETF 行。

**Architecture:** 新的每日任务 `stock_data`（21:15 ET）用 Finnhub 为“标普 500 成分 ∪ 持仓”预取公司新闻（每天全部）与财务指标（每天最旧的 120 只），每只写一个 `data_dir/market/stocks/{SYMBOL}.json`。同板块、市值、板块与板块 ETF 区间收益不新增请求：取第 1 批的 GICS 表（加子行业）、`caps.json` 与快照（新增 `sector_periods`）。新接口 `GET /api/stock/{symbol}/extras` 只读这些文件。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pandas、React 19 + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 5.4（个股详情：右栏估值与财务、相关新闻、同板块；新闻标签；区间表现对比板块 ETF），7.6，9.1。前两批计划：`docs/superpowers/plans/2026-11-01-market-board-batch1.md`、`...-batch2.md`。

## Global Constraints

- 面板只读，唯一写操作是提交回测；新接口只有 GET（`tests/test_api_readonly.py`）。
- key 只在服务器 `.env`；Finnhub key 只在请求头。免费数据源的数据不提交仓库，测试只用手写样本。
- 业务时间美东；交易日以 `XNYS` 为准。
- 布局：12 列网格、间距 12px、同排等高、表格不换行；1280px 与 1600px 下检查。
- 只覆盖“标普 500 成分 ∪ 持仓”（ETF 除外）；其他代码在面板上写明“仅覆盖标普 500 成分股与持仓”，不是报错。

## 服务器实测（2026-11-01）

- Finnhub `/stock/metric?symbol=&metric=all`：200，约 0.25 秒，`{"metric": {...}, "series": {...}}`，`metric` 约 110–133 项。字段与单位（AAPL / JPM / BRK.B 实测）：`peTTM` 39.01、`forwardPE` 35.34、`pbQuarterly` 38.49、`psTTM` 10.77、`evEbitdaTTM` 30.21（JPM 为 null）、`currentDividendYieldTTM` 0.311（**百分数**，即 0.311%）、`roeTTM` 137.18（百分数）、`grossMarginTTM` 48.65（百分数，JPM 为 null）、`operatingMarginTTM`、`netProfitMarginTTM`、`revenueGrowthTTMYoy` 14.24（百分数）、`epsGrowthTTMYoy`、`totalDebt/totalEquityQuarterly` 0.78（倍数）、`marketCapitalization`（百万美元）。`BRK.B` 可直接查询。
- 标普 500 GICS 数据集的列：`Symbol,Security,GICS Sector,GICS Sub-Industry,...`；服务器上 `market/sp500_gics.csv` 存的是整份 CSV，已含子行业。

## 决定

- **估值与财务用 Finnhub `/stock/metric`**（设计 9.1 的备选）：一次请求就有现成的比率，覆盖 500 只每周一轮；EDGAR companyfacts 需要自己算 TTM 与市盈率，工作量大得多。展示项（键、标签、类型）固定为：

  | 键 | 标签 | 类型 |
  |---|---|---|
  | peTTM | 市盈率（TTM） | ratio |
  | forwardPE | 远期市盈率 | ratio |
  | pbQuarterly | 市净率 | ratio |
  | psTTM | 市销率（TTM） | ratio |
  | evEbitdaTTM | EV / EBITDA | ratio |
  | currentDividendYieldTTM | 股息率 | pct |
  | roeTTM | ROE（TTM） | pct |
  | grossMarginTTM | 毛利率 | pct |
  | operatingMarginTTM | 营业利润率 | pct |
  | netProfitMarginTTM | 净利率 | pct |
  | revenueGrowthTTMYoy | 营收增长（TTM 同比） | pct |
  | epsGrowthTTMYoy | EPS 增长（TTM 同比） | pct |
  | totalDebt/totalEquityQuarterly | 负债 / 股东权益 | ratio |

  `pct` 类在接口里除以 100 变成小数（前端按百分比格式化）。
- **新闻**：每只取 `company_news(sym, today − 7 天, today + 1 天)`，去重（小写标题）后保留最新 15 条；分类用第 2 批的 `EARNINGS_RE`（`earnings` / `company`）。
- **请求预算**：Finnhub 每分钟 60 次，客户端间隔 1.1 秒。每次任务：新闻全部覆盖股（约 510 次）+ 指标最多 120 只，约 12 分钟。顺序：持仓优先，然后按上次更新时间从旧到新（从未取过的最先）。限流或临时故障时停止剩余请求、保留已写文件，下次从最旧的继续；401 抛出（任务失败告警）。
- **同板块**：同一 GICS 子行业的其他成分股，按市值降序取前 8；少于 3 只时用同板块（GICS Sector）的补到 8 只。每行：代码、名称、市值、1日、今年（取快照热力图的 `1D` / `YTD`）。
- **板块 ETF 区间收益**：快照新增 `sector_periods: {ETF: {"1D","1W","1M","3M","6M","YTD","1Y"}}`，口径与个股页一致（复用 `quant.api.analytics.period_returns`，它是只依赖 pandas 的纯函数）。
- 顺手修第 2 批验收发现的显示问题：财报 EPS 保留 2 位小数。

## Review Focus

1. 网址里的代码不合法（`../x`、超长、含空格）：接口按“未覆盖”返回，不读数据目录以外的文件，不 500。——测试：`test_extras_rejects_bad_symbol`（Task 4）。
2. 未覆盖的代码（QQQ、非成分股）：`covered=false`，板块 / 同板块为空，前端写明覆盖范围。——测试：`test_extras_uncovered_symbol`（Task 4）、`stock extras uncovered`（Task 5）。
3. 任务中途 429：已写的文件保留；下一次从最旧的开始，不会总是同一批股票拿不到。——测试：`test_stock_data_resumes_oldest_first`（Task 3）。
4. 指标为 null（JPM 毛利率、EV/EBITDA）：显示“—”，其余照常。——测试：`test_extras_metrics_scaled_and_null`（Task 4）。
5. 快照没有 `sector_periods`（新代码部署后、market_data 运行前的旧快照）或快照不存在：板块 ETF 行显示“—”，同板块收益为空，不报错。——测试：`test_extras_old_snapshot`（Task 4）。

---

### Task 1: 财报 EPS 保留 2 位小数

**Files:** Modify `src/quant/market/calendar.py`（`_num`）；Test `tests/test_market_calendar.py`

- [ ] **Step 1: 写失败测试** `test_eps_rounded`：`EarningsRaw("AAPL", date(2026, 11, 10), "amc", 2.0221, None)`，持仓含 AAPL，`build_calendar` 的接下来那一行 `forecast == "2.02"`；`3.8` 仍为 `"3.8"`，`1.95` 为 `"1.95"`。
- [ ] **Step 2: 运行确认失败** — `uv run pytest -q tests/test_market_calendar.py` → FAIL
- [ ] **Step 3: 实现** — `_num` 改为 `f"{round(v, 2):g}"`
- [ ] **Step 4: 运行确认通过** → PASS
- [ ] **Step 5: 提交** — `fix: 财经日历 EPS 保留 2 位小数（Refs #70）`

### Task 2: GICS 子行业与板块 ETF 区间收益

**Files:** Modify `src/quant/data/market_store.py`（`GicsTable`、`parse_gics`、新增 `load_gics`）、`src/quant/market/board.py`（`MarketSnapshot.sector_periods`、`build_snapshot`）、`src/quant/api/schemas.py`（`MarketOverview.sector_periods`）；Test `tests/test_market_store.py`、`tests/test_market_board.py`

**Interfaces:**
- Produces:
  - `GicsTable.sub: dict[str, str]`（默认空）与 `GicsTable.sub_industry(symbol) -> str | None`；`parse_gics` 有 `GICS Sub-Industry` 列时读入，没有时为空（旧样本兼容）
  - `load_gics(data_dir: Path) -> GicsTable | None`（只读本地文件，不下载；缺失或无法解析为 None）
  - `MarketSnapshot.sector_periods: dict[str, dict[str, float | None]]`（键为 11 个板块 ETF）

- [ ] **Step 1: 写失败测试**
  - `test_parse_gics_sub_industry`：带子行业列的 CSV → `sub_industry("AAPL") == "Technology Hardware, Storage & Peripherals"`；不带该列的旧 CSV → `None`
  - `test_load_gics`：没有文件 → None；有文件 → 表；内容损坏 → None
  - `test_snapshot_sector_periods`：用 `test_market_board.py` 的合成数据，`snap.sector_periods["XLK"]` 的键为 `["1D","1W","1M","3M","6M","YTD","1Y"]`，`1D` 等于 `sectors` 里 XLK 的 `d1`
- [ ] **Step 2: 运行确认失败** — `uv run pytest -q tests/test_market_store.py tests/test_market_board.py` → FAIL
- [ ] **Step 3: 实现**（`build_snapshot` 中对每个板块 ETF 调 `period_returns(close[etf].dropna(), as_of)`；`MarketOverview` 加 `sector_periods: dict[str, dict[str, float | None]] = {}`）
- [ ] **Step 4: 运行确认通过** — 同上加 `tests/test_api_market.py` → PASS
- [ ] **Step 5: 提交** — `feat: GICS 子行业与板块 ETF 区间收益（Refs #70）`

### Task 3: Finnhub 财务指标与每日任务 stock_data

**Files:**
- Modify: `src/quant/data/providers/finnhub.py`（`metrics`）、`src/quant/data/market_store.py`（`stock_path`、`STOCK_SYMBOL_RE`）、`src/quant/jobs/daily.py`、`src/quant/jobs/scheduler.py`、`src/quant/core/settings.py`、`tests/test_jobs.py`、`deploy/server_init.md`、`docs/PROJECT_PLAN.md`
- Create: `src/quant/market/stock_job.py`
- Test: `tests/test_finnhub.py`、`tests/test_stock_job.py`

**Interfaces:**
- Consumes: `FinnhubClient`、`NewsRaw`、`EARNINGS_RE`、`load_gics`（Task 2）、`atomic_write_text`
- Produces:
  - `FinnhubClient.metrics(symbol: str) -> dict[str, float | None]`（返回 `body["metric"]` 中 `METRIC_KEYS` 这些键；非数字为 None）
  - `quant/market/stock_job.py`：`METRICS: tuple[tuple[str, str, str], ...]`（键、标签、类型，即“决定”中的表）、`METRIC_KEYS`
  - `STOCK_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")`；`stock_path(data_dir, symbol) -> Path | None`（不合法返回 None）
  - 每只文件格式：`{"symbol", "news_updated": ISO|None, "news": [NewsItem 字段...], "metrics_updated": "YYYY-MM-DD"|None, "metrics": {键: 原始值}}`
  - `run_stock_data(*, data_dir, today: date, now: datetime, holdings: set[str], finnhub: FinnhubLike, metrics_per_run: int = 120) -> str`
  - `daily.stock_data(ctx, d)`：没有 Finnhub key 时返回“未配置…跳过”；`ScheduleSettings.stock_data = "21:15"`；`JOBS`、`JOB_FUNCS`、`day_plan` 增加

- [ ] **Step 1: 写失败测试**

```python
# tests/test_finnhub.py
def test_metrics_selected_keys():
    body = {"metric": {"peTTM": 39.01, "grossMarginTTM": None, "roeTTM": "x", "other": 1}, "series": {}}
    fetch = FakeFetch((200, body))
    got = client(fetch).metrics("BRK.B")
    assert fetch.calls[0][0] == "https://finnhub.io/api/v1/stock/metric?symbol=BRK.B&metric=all"
    assert got["peTTM"] == 39.01 and got["grossMarginTTM"] is None and got["roeTTM"] is None
    assert "other" not in got and set(got) == set(METRIC_KEYS)

# tests/test_stock_job.py（假 Finnhub：company_news / metrics 记录调用，可设定第 N 次起抛 DataUnavailableError 或 FinnhubAuthError）
def test_stock_data_writes_files(tmp_path): ...
    # GICS 表 3 只（S0..S2）+ 持仓 {"HOLD"}：4 个文件；news 按时间降序、≤15、去重；metrics 有 13 个键；
    # 摘要含 “新闻 4 只” 与 “指标 4 只”
def test_stock_data_holdings_first_and_metrics_cap(tmp_path): ...
    # 10 只成分 + 持仓 H：metrics_per_run=3 → metrics 调用 == ["H", 再加最旧的 2 只]；新闻调用首个为 "H"
def test_stock_data_resumes_oldest_first(tmp_path): ...
    # 第一次第 4 次新闻请求起限流：已写 3 只、不抛异常、摘要含 “限流或故障”；
    # 第二次：新闻请求顺序以上次没取到的（news_updated 为 None）开头
def test_stock_data_auth_error_raises(tmp_path): ...
def test_stock_data_skips_etf_and_bad_symbols(tmp_path): ...
    # 持仓含 "QQQ"（ETF_SET）与 "bad sym"：不请求、不写文件
def test_stock_path_validation():
    assert stock_path(Path("/d"), "BRK.B") == Path("/d/market/stocks/BRK.B.json")
    assert stock_path(Path("/d"), "../etc") is None and stock_path(Path("/d"), "aapl") is None
def test_scheduler_includes_stock_data(): ...   # 21:15 ET；JOBS / JOB_FUNCS 含 stock_data
```

执行者把每个 `...` 写成完整测试，断言按注释；`tests/test_jobs.py::test_day_plan_regular` 补 `"stock_data": et(DAY, 21, 15)`。

- [ ] **Step 2: 运行确认失败** — `uv run pytest -q tests/test_finnhub.py tests/test_stock_job.py` → FAIL
- [ ] **Step 3: 实现**：覆盖集 = `load_gics` 成员 ∪ 持仓，去掉 `ETF_SET` 与不合法代码（没有 GICS 表时只有持仓）；新闻与指标各自按上次更新时间排序（持仓优先），指标只取 `metrics_updated` 早于 `today − 6 天` 或从未取过的、最多 `metrics_per_run` 只；每只先读旧文件再更新对应部分并原子写回；限流 / 临时故障停止整个任务剩余请求。`market_dir/stocks/` 由写入时创建。文档：`deploy/server_init.md` 与 `docs/PROJECT_PLAN.md` 任务表加一行（21:15，约 12 分钟）。
- [ ] **Step 4: 运行确认通过** — `uv run pytest -q tests/test_finnhub.py tests/test_stock_job.py tests/test_jobs.py` → PASS
- [ ] **Step 5: 提交** — `feat: 每日任务 stock_data 预取个股新闻与财务指标（Refs #69 #70）`

### Task 4: 接口 `/api/stock/{symbol}/extras`

**Files:** Modify `src/quant/api/routes/stock.py`、`src/quant/api/schemas.py`；Create `src/quant/api/stock_extras.py`；Test `tests/test_api_stock_extras.py`

**Interfaces:**
- Consumes: `stock_path`、`load_gics`、`gics_path`、`MarketStore.caps`、`snapshot_path`、`SECTOR_ETF`、`ETF_SET`、`METRICS`
- Produces:
  - `MetricOut {key: str, label: str, kind: Literal["ratio","pct"], value: float | None}`（pct 已除以 100）
  - `PeerOut {symbol, name: str | None, market_cap: float | None, d1: float | None, ytd: float | None}`
  - `StockExtras {symbol, covered: bool, name: str | None, sector: str | None（中文）, sub_industry: str | None, market_cap: float | None, sector_etf: str | None, sector_periods: dict[str, float | None], peers: list[PeerOut], metrics: list[MetricOut], metrics_updated: date | None, news: list[NewsItemOut], news_updated: datetime | None}`

- [ ] **Step 1: 写失败测试**
  - `test_extras_covered_symbol`：GICS 表（AAPL 与同子行业 3 只、同板块另 5 只）、caps、快照（`heatmap` 的 1D/YTD、`sector_periods`）与 `stocks/AAPL.json` → `covered`、`sector == "信息技术"`、`sector_etf == "XLK"`、`sector_periods["1D"]` 来自快照、`peers` 不含 AAPL 且同子行业的排前、按市值降序、最多 8 只、`news` 原样
  - `test_extras_metrics_scaled_and_null`：`roeTTM 137.18` → `value == 1.3718`；`grossMarginTTM null` → None；顺序与 `METRICS` 一致、共 13 项
  - `test_extras_uncovered_symbol`：`QQQ` → `covered=false`、`peers == []`、`metrics == []`，状态码 200
  - `test_extras_rejects_bad_symbol`：`/api/stock/..%2Fx/extras` 与 `/api/stock/A%20B/extras` → 200 或 404，均不 500；数据目录外放一个同名文件也不会被读到
  - `test_extras_old_snapshot`：快照没有 `sector_periods` / 没有快照 → `sector_periods == {}`、peers 的 `d1`、`ytd` 为 None
- [ ] **Step 2: 运行确认失败** — `uv run pytest -q tests/test_api_stock_extras.py` → FAIL
- [ ] **Step 3: 实现**（代码先 `upper()` 再用 `STOCK_SYMBOL_RE` 校验；不合法按未覆盖返回；所有文件读取失败都降级为空，不 500）
- [ ] **Step 4: 运行确认通过** — `uv run pytest -q tests/test_api_stock_extras.py tests/test_api_readonly.py` → PASS；`make api-types`
- [ ] **Step 5: 提交** — `feat: 个股页估值、新闻、同板块接口（Refs #70）`

### Task 5: 前端个股页

**Files:** Modify `web/src/pages/StockPage.tsx`、`web/src/styles/layout.css`；Create `web/src/components/StockNews.tsx`；Test `web/src/pages/StockPage.test.tsx`

页面行为：
- 区间表现第三行：“板块 ETF · XLK 信息技术”，各列取 `sector_periods`；没有时整行“—”。
- 关键数据：“市值”行（`compact`，前加 `$`）、“板块”行（中文板块；子行业放 `title` 提示）；未覆盖时为“—”。
- 估值与财务：两列表（标签、数值；ratio 保留 2 位加 “×”，pct 用 `pct(v, 1)`），右上角“更新于 MM-DD”；未覆盖写“仅覆盖标普 500 成分股与持仓”；覆盖但还没取到写“数据接入中（每天补 120 只）”。
- 相关新闻（右栏）：最新 5 条（时间 MM-DD HH:MM ET、来源、标题链接，新窗口、`rel="noreferrer"`）；“新闻”标签页：全部（最多 15 条），带“财报”标签。
- 同板块：表格（代码链接到个股页并带 account、名称、市值、1日、今年）；为空时同“估值与财务”的两种文案。

- [ ] **Step 1: 写失败测试**：`stock extras sector etf row`、`stock extras key stats cap and sector`、`stock extras metrics`（“39.01×”、“137.2%”、null 显示“—”、“更新于 10-30”）、`stock extras news panel and tab`、`stock extras peers link with account`、`stock extras uncovered`（三处出现“仅覆盖标普 500 成分股与持仓”）
- [ ] **Step 2: 运行确认失败** — `cd web && npx vitest run src/pages/StockPage.test.tsx` → FAIL
- [ ] **Step 3: 实现**（新闻列表组件只导出组件；`/api/stock/{symbol}/extras` 失败时各块显示“数据接入中”）
- [ ] **Step 4: 运行确认通过** — `make web-check` → PASS
- [ ] **Step 5: 提交** — `feat: 个股页估值与财务、新闻、同板块（Refs #70）`

### Task 6: 联调与验收

- [ ] **Step 1**：`make check` 全部通过（后端带 `QUANT_TEST_DATABASE_URL`）。
- [ ] **Step 2**：合成数据（沿用第 2 批的 `mkt2`，加 `stocks/*.json` 与子行业）启动面板，在 1280px / 1600px 截个股页：右栏等高、表格不换行、长标题省略。
- [ ] **Step 3**：部署后服务器手动运行 `market_data`（生成带 `sector_periods` 的快照）与 `stock_data`，记录耗时、覆盖只数、AAPL / JPM / BRK.B 页面核对，写入 #70。
