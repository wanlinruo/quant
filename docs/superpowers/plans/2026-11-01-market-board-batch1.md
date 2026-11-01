# 首页市场看板 第 1 批（Massive 全市场日线 + GICS 板块）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页上线行情条（ETF 部分）、今日要点、标普 500 热力图、市场宽度、风格与因子、异动个股、指数日线、板块表现；数据来自 Massive 全市场日线与标普 500 GICS 分类，由每日任务预计算，接口只读。Refs #69 #70。

**Architecture:** scheduler 新增每日任务 `market_data`（收盘后）：同步 GICS 成分表（每周）、取当日 Massive 全市场日线并按额度补历史、分批刷新市值，然后用纯计算模块生成一份首页快照 JSON；面板接口只读这份快照；前端按原型 v4 重做首页（新闻、财经日历、宏观行情项留到第 2 批，显示“数据接入中”）。

**Tech Stack:** Python 3.12、urllib、pandas、FastAPI；React + TypeScript（热力图用 SVG 自写矩形树图，不加依赖）。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md` 第 5.1、6、7.6、9.1 节；原型 `docs/superpowers/specs/2026-10-31-dashboard-redesign-prototype.html` 的 `#p-market`；#60–#62 结论（Massive 全市场日线、Massive 市值、标普 500 GICS 数据集）。

## Global Constraints

- Massive 免费档每分钟 5 次请求、历史 2 年、收盘后数据；客户端必须限速（两次请求间隔 ≥ 12.5 秒），429 时退避并停止本轮。
- key 只来自环境变量（`SecretStr`），请求头 `Authorization: Bearer <key>`，不进 URL、日志、异常、接口返回；仓库公开，**免费数据不得提交到仓库**（测试只用合成数据）。
- 数据仅个人使用、不再分发：面板在登录保护之后。
- 首页“避免每次打开扫描 500 只日线”（7.6）：所有计算在任务里完成，接口只读快照文件。
- 业务时间按美东；交易日以 XNYS 日历为准。
- 布局（设计第 6 节）：12 列网格、间距 12px、同排等高、表格不换行、1280 / 1600px 检查对齐；绿涨红跌，数字 `tabular-nums`，百分比带正负号。
- 面板只读；scheduler 的新任务不触碰交易逻辑、不需要券商连接。
- GICS 数据集许可为 ODC-PDDL（公共领域），可使用；仍不提交下载结果。

## 我做的决定（用户审阅时可推翻）

1. **范围**：本批只做设计 5.1 中能由 Massive 日线 + GICS 支撑的模块。行情条 10 项里，标普 500 / 纳指 / 道指 / 罗素 2000 / 黄金用 ETF 代理（SPY、QQQ、DIA、IWM、GLD，标签注明“ETF”）；VIX、美债 10 年、美元指数、原油、比特币显示“数据接入中”（第 2 批 FRED / Coinbase）。异动个股“原因”列显示“—”（第 2 批新闻）。热力图只做“标普 500”（纳指 100 无公开成分名单；“我的候选池”就是标普 500）。
2. **标的范围**：存储与计算只用 当前标普 500 成分 ∪ 固定 ETF 集合（指数 4 只、11 只 SPDR 板块、风格 IWF / IWD / RSP / MTUM / SPHB / SPLV、GLD）∪ 各账户当前持仓；全市场 1.2 万只不落盘。
3. **历史长度**：补到最近 260 个交易日（52 周新高新低、200 日线、YTD、1 年 K 线够用），首次补齐约 260 次请求；每次任务最多补 `history_per_run=60` 天（约 13 分钟），四五天补齐，之后每天 1 次。
4. **市值**：Massive `/v3/reference/tickers/{T}`，每次任务最多刷新 60 只、每只 7 天内不重复，约一周轮完一遍；拿不到的用最近一次。
5. **任务时间**：`market_data` 在 20:30 ET（数据由交易所收盘后整理，盘后约 4 小时足够）；当日数据为空时不算失败，沿用前一快照并在快照里标 `stale: true`，次日补拉。
6. **风格因子**：成长−价值 = IWF−IWD；大盘−小盘 = SPY−IWM；市值−等权 = SPY−RSP；动量 = MTUM−SPY；高贝塔−低波 = SPHB−SPLV；各给 1 日、1 月差值。
7. **异动信号**（S&P 成分）：放量突破（量比 ≥ 2 且收盘为 20 日最高）、跳空（|开盘 / 前收 − 1| ≥ 3%）、52 周新高 / 新低、跌破 50 日线（今收 < MA50 且前收 ≥ 前 MA50）。快照里保留 |涨跌| ≥ 2% 或有任一信号的成分，按 |涨跌| 排序、最多 150 只；前端做筛选。
8. **今日要点**（规则拼句，不用 AI）：“标普 500（SPY）{±x%} 收于 {close}，距 52 周高点 {−y%}；{领涨板块} 领涨、{领跌板块} 领跌；上涨家数 {z%}；52 周新高 {a} 只、新低 {b} 只。”

## Review Focus

- 新成分 / 刚上市股票历史不足 200 天：均线、52 周指标按“数据不足”跳过，不抛错、不计入比例分母。→ Task 3 `test_breadth_skips_short_history`。
- 当日 Massive 数据为空（节假日后、延迟发布）或任务首次运行历史为空：快照仍生成（`stale` 或各模块空列表），首页显示“数据接入中”或“数据截至 X”，不白屏。→ Task 4 `test_job_keeps_previous_snapshot_when_day_empty`；Task 6 `shows pending when snapshot missing`。
- 成分股文件里的代码与 Massive 代码写法不一致（BRK.B / BF.B 带点，均实测与 Massive 一致）或成分名单下载失败：沿用上次成功的文件，缺代码的不计入热力图，不中断任务。→ Task 2 `test_sync_keeps_previous_on_failure`。
- 429 / 网络超时：停止本轮剩余请求、已取数据保留、任务成功结束（下次继续）。→ Task 1 `test_429_stops_and_raises_rate_limited`；Task 4 `test_job_stops_backfill_on_rate_limit`。
- 快照文件被任务写到一半时接口读取：原子写，接口要么读到旧的要么读到新的。→ Task 4 `test_snapshot_written_atomically`。

---

### Task 1: Massive 客户端

**Files:**
- Create: `src/quant/data/providers/massive.py`
- Modify: `src/quant/core/settings.py`（`massive_api_key: SecretStr | None`）、`.env.example`
- Test: `tests/test_massive.py`

**Interfaces:**
- Produces:
  - `class MassiveRateLimited(DataUnavailableError)`；`class MassiveAuthError(DataUnavailableError)`
  - `class MassiveClient`：`__init__(api_key: SecretStr, fetch: Fetch = http_fetch, sleep=time.sleep, clock=time.monotonic, min_interval: float = 12.5)`；
    - `grouped_daily(day: date) -> pd.DataFrame`：GET `https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{day}?adjusted=true`，返回列 `symbol, open, high, low, close, volume, vwap`（`T,o,h,l,c,v,vw`），空结果返回空表（不是异常）
    - `ticker_details(symbol: str) -> TickerDetails | None`（`name`、`market_cap`、`sic_description`；404 → None）
  - 复用 `quant.data.providers.tiingo.http_fetch` 的签名（`(url, headers) -> (status, body)`），把它挪到 `providers/http.py` 供两者共用
- Behavior：两次请求间隔不少于 `min_interval`（不足则 `sleep`）；429 → `MassiveRateLimited`；401/403 → `MassiveAuthError`；其它非 200 或网络异常 → `DataUnavailableError`，错误信息不含 key

- [ ] **Step 1: 写失败测试**：`test_grouped_daily_maps_fields`、`test_empty_day_returns_empty_frame`、`test_auth_header_not_in_url`、`test_rate_limit_spacing`（假时钟：连续两次请求，第二次前 `sleep(12.5 - 已过时间)`）、`test_429_stops_and_raises_rate_limited`、`test_401_raises_auth`、`test_ticker_details_404_is_none`、`test_key_not_in_error_message`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_massive.py tests/test_tiingo.py` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat: Massive 全市场日线与个股资料客户端（Refs #69）`

### Task 2: 标普 500 GICS 成分表与市场数据存储

**Files:**
- Create: `src/quant/data/market_store.py`
- Test: `tests/test_market_store.py`

**Interfaces:**
- Consumes: Task 1 `MassiveClient`
- Produces:
  - `MARKET_DIR = "market"`（在 `data_dir` 下）；`ETF_SET: dict[str, tuple[str, str]]`（代码 → （中文名, 组：index / sector / style / other)），含 SPY 标普 500、QQQ 纳指 100、DIA 道指、IWM 罗素 2000、GLD 黄金、11 只 SPDR（XLK 信息技术、XLF 金融、XLV 医疗、XLY 可选消费、XLP 必需消费、XLE 能源、XLI 工业、XLB 材料、XLU 公用事业、XLRE 房地产、XLC 通信服务）、IWF、IWD、RSP、MTUM、SPHB、SPLV
  - `SECTOR_ETF: dict[str, str]`（GICS 板块英文名 → 板块 ETF）
  - `GICS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"`
  - `sync_gics(data_dir: Path, today: date, fetch_text: Callable[[str], str] = http_get_text, max_age_days: int = 7) -> GicsTable`：文件超过 7 天才下载；下载或解析失败（列不全、行数 < 400）沿用旧文件，没有旧文件则抛 `DataUnavailableError`
  - `GicsTable`：`members: list[str]`、`sector(symbol) -> str | None`、`name(symbol) -> str | None`
  - `class MarketStore(data_dir)`：
    - `write_day(day: date, frame: pd.DataFrame, keep: set[str]) -> int`（只保留 `keep` 中的代码，原子写 `market/daily/{day}.parquet`）
    - `days() -> list[date]`、`panel(field: str, symbols: Sequence[str] | None = None, last_n: int | None = None) -> pd.DataFrame`（日期 × 代码宽表）
    - `caps() -> dict[str, CapInfo]`、`write_caps(dict)`（`CapInfo(market_cap, name, updated)`，原子写 JSON）

- [ ] **Step 1: 写失败测试**：`test_parse_gics_and_sector_lookup`（合成 CSV 3 行）、`test_sync_skips_when_fresh`、`test_sync_keeps_previous_on_failure`（下载抛错 → 返回旧表；格式错误 → 旧表）、`test_sync_without_previous_raises`、`test_write_day_filters_and_panel`（写 3 天，`panel("close", ["SPY"])` 形状正确）、`test_caps_roundtrip`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_market_store.py` — Expected: FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 标普 500 GICS 成分表与首页市场数据存储（Refs #69）`

### Task 3: 首页快照的纯计算

**Files:**
- Create: `src/quant/market/__init__.py`、`src/quant/market/board.py`
- Test: `tests/test_market_board.py`

**Interfaces:**
- Consumes: Task 2 的宽表（`close`、`open`、`high`、`low`、`volume`）、`GicsTable`、`caps`
- Produces（纯函数，输入宽表，输出可 JSON 序列化的 dataclass，样本不足的项为 None）：
  - `ticker_strip(close: pd.DataFrame) -> list[TickerItem]`（ETF_SET 中 group=index 的 4 只 + GLD：`symbol`、`label`、`close`、`change`、`change_pct`、`spark`（近 30 日收盘））
  - `breadth(close, high, low, volume, members) -> Breadth`：`advancers`、`decliners`、`unchanged`、`new_highs`、`new_lows`（52 周 = 252 日）、`above_50`、`above_200`（比例，分母只含历史足够的）、`up_volume_ratio`，每项再给 `prev`（前一日同口径）
  - `styles(close) -> list[StylePair]`（“我做的决定”第 6 条，`d1`、`m1` 为两腿收益之差）
  - `sectors(close) -> list[SectorRow]`（11 只：`symbol`、`name`、`d1`、`w1`、`m1`、`ytd`、`rel_spy_d1`、`spark`（近 1 月）），按 `d1` 降序
  - `movers(close, open_, high, volume, gics, caps) -> list[Mover]`（第 7 条；`symbol`、`name`、`sector`、`market_cap`、`close`、`change_pct`、`volume_ratio`、`signals: list[str]`、`spark`（20 日））
  - `heatmap(close, gics, caps) -> dict[str, list[HeatCell]]`（键 `1D`、`1W`、`1M`、`YTD`；`symbol`、`name`、`sector`、`market_cap`、`ret`；没有市值的跳过）
  - `index_candles(close, open_, high, low, volume, symbols=("SPY","QQQ","DIA","IWM"), days=252) -> dict[str, list[Candle]]`
  - `highlights(strip, sectors, breadth, close) -> str`（第 8 条）
  - `build_snapshot(store: MarketStore, gics: GicsTable, as_of: date, stale: bool) -> MarketSnapshot`（汇总以上，含 `as_of`、`stale`、`generated_at`、`members`、`coverage`（成分中有当日数据的比例））

- [ ] **Step 1: 写失败测试**（合成 300 个交易日、6 只成分 + 所需 ETF；数值手算）：`test_ticker_strip_change_and_spark`、`test_breadth_counts_and_prev`、`test_breadth_skips_short_history`（一只只有 30 天历史：`above_200` 分母不含它）、`test_styles_pairs`、`test_sectors_sorted_and_rel`、`test_movers_signals`（构造放量突破、跳空、新高、跌破 50 日线各一只）、`test_heatmap_skips_missing_cap`、`test_highlights_text`（断言完整句子）、`test_snapshot_json_serializable_no_nan`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_market_board.py` — Expected: FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 首页市场看板的预计算（行情条、宽度、风格、板块、异动、热力图、要点）（Refs #70）`

### Task 4: 每日任务 market_data

**Files:**
- Modify: `src/quant/jobs/daily.py`（`market_data`，加入 `JOB_FUNCS`）、`src/quant/jobs/scheduler.py`（加入日程）、`src/quant/core/settings.py`（`ScheduleSettings.market_data = "20:30"`；`BoardSettings` 段，挂在 `Settings.board`（已有的 `market` 段是交易时段配置，不能复用）：`history_days=260`、`history_per_run=60`、`caps_per_run=60`、`caps_max_age_days=7`）、`src/quant/cli.py`（`quant job` 可选任务加入 `market_data`）、`configs/base.yaml`
- Create: `src/quant/market/job.py`（任务主体，`daily.py` 只做薄封装）
- Test: `tests/test_market_job.py`

**Interfaces:**
- Consumes: Task 1–3
- Produces:
  - `run_market_data(client: MassiveClient, data_dir: Path, today: date, cal: TradingCalendar, settings: BoardSettings, holdings: set[str], fetch_text=http_get_text) -> str`（返回摘要）：
    1. `sync_gics`；`keep = members ∪ ETF_SET ∪ holdings`
    2. 取 `today` 的 grouped；为空 → `stale=True`
    3. 补历史：从最近缺失的交易日往前，最多 `history_per_run` 天，直到有 `history_days` 天
    4. 刷新市值：成分中 `updated` 最旧的 `caps_per_run` 只（超过 `caps_max_age_days`）
    5. `build_snapshot` → 原子写 `market/snapshot.json`
    - `MassiveRateLimited` / 网络故障：停止剩余请求，已取数据保留，照常生成快照；`MassiveAuthError` → 抛出（任务失败并告警，属配置问题）
  - `daily.market_data(ctx, d)`：没有配置 `massive_api_key` 时返回“未配置 Massive key，跳过”（不失败）；持仓 = 各 mode 最新持仓快照的代码并集
  - 日程：交易日 `schedule.market_data`，不依赖其它任务

- [ ] **Step 1: 写失败测试**（假 MassiveClient，合成数据）：`test_job_first_run_builds_snapshot`、`test_job_keeps_previous_snapshot_when_day_empty`（`stale` 为 true，`as_of` 为上一交易日）、`test_job_stops_backfill_on_rate_limit`、`test_job_refreshes_oldest_caps_first`、`test_snapshot_written_atomically`、`test_job_without_key_skips`、`test_scheduler_includes_market_data`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_market_job.py tests/test_jobs.py` — Expected: 新测试 FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行** 同上 — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 每日任务 market_data 生成首页市场快照（Refs #69）`

### Task 5: 首页接口

**Files:**
- Modify: `src/quant/api/routes/market.py`（`GET /api/market/overview`）、`src/quant/api/schemas.py`
- Test: `tests/test_api_market.py`

**Interfaces:**
- Consumes: Task 3 的 `MarketSnapshot` 结构（JSON）
- Produces: `GET /api/market/overview` → `MarketOverview`（字段与快照一致；文件不存在时 `status="pending"`、其余为空；JSON 损坏时同样返回 pending 并记日志，不 500）

- [ ] **Step 1: 写失败测试**：`test_overview_pending_without_snapshot`、`test_overview_reads_snapshot`、`test_overview_corrupt_snapshot_is_pending`。
- [ ] **Step 2: 运行** `uv run pytest -q tests/test_api_market.py tests/test_api_readonly.py` — Expected: FAIL。
- [ ] **Step 3: 实现**；`make api-types`。
- [ ] **Step 4: 运行** 同上 + `tests/test_api_app.py` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 首页市场看板接口（Refs #70）`

### Task 6: 首页前端

**Files:**
- Modify: `web/src/pages/MarketPage.tsx`、`web/src/pages/MarketPage.test.tsx`、`web/src/styles/layout.css`
- Create: `web/src/components/Treemap.tsx`（矩形树图，squarified 算法，SVG）、`web/src/components/Sparkline.tsx`

**Interfaces:**
- Consumes: Task 5 接口；现有 `/api/portfolio/{mode}/overview`、`/holdings`（“我的持仓”筛选）、`/api/signals/latest`、`/api/system/jobs`

布局（12 列，对照原型 v4）：
1. 行情条 12：10 格等宽（5 个 ETF 有数，另 5 个“数据接入中”）；下方一行“今日要点”与“数据截至 {as_of}（stale 时黄色提示）”
2. 主区 9 + 右栏 3：
   - 主区：热力图 8 + 市场宽度 / 风格与因子 4（上下两块）；异动个股 12（筛选：综合 / 涨幅 / 跌幅 / 放量 / 跳空 / 新高低 / 我的持仓；点代码进个股页并保留 `?account=`）；指数日线 6（SPY / QQQ / DIA / IWM 切换，MA20 / MA50）+ 板块表现 6
   - 右栏：新闻（数据接入中）、财经日历（数据接入中）、我的策略（沿用现有，按账户）
3. 热力图：按板块分组的矩形树图，面积 = 市值，颜色 = 涨跌（绿涨红跌，±3% 饱和），1日 / 1周 / 1月 / YTD 切换，悬停显示代码、名称、涨跌，点击进个股页

- [ ] **Step 1: 写失败测试**：`shows pending when snapshot missing`、`renders ticker strip with pending macro items`（SPY 有数，VIX 显示“数据接入中”）、`shows stale warning`、`movers filter by signal`、`movers holdings filter uses account holdings`、`heatmap period switch`、`treemap cells link to stock page with account`、`highlights text shown`。
- [ ] **Step 2: 运行** `cd web && npx vitest run src/pages/MarketPage.test.tsx` — Expected: FAIL。
- [ ] **Step 3: 实现**（Treemap 另有 `Treemap.test.tsx`：面积与输入成比例、总面积等于画布）。
- [ ] **Step 4: 运行** `make web-check` — Expected: PASS。
- [ ] **Step 5: Commit** `feat: 首页市场看板按原型重做（第 1 批）（Refs #70）`

### Task 7: 部署配置、文档与验收

**Files:**
- Modify: `compose.yml`（scheduler 增加 `QUANT_MASSIVE_API_KEY: ${MASSIVE_API_KEY:-}`）、`deploy/server_init.md`、`docs/PROJECT_PLAN.md`（第 7、8、10 章：market_data 任务、`market/` 目录、首页数据来源）

- [ ] **Step 1: 全量检查** `make check` — Expected: 全部通过。
- [ ] **Step 2: 本地联调**：临时数据目录 + 合成快照（或在 Mac 用自己的 key 跑一次 `quant job market_data`，数据只放临时目录、不提交），截图 `/` 在 1280 与 1600 宽度检查对齐。
- [ ] **Step 3: Commit** `chore: market_data 任务部署配置与文档（Refs #69 #70）`
- [ ] **Step 4: 部署后验收**（合并部署后，经 ssh 只读）：首次手动 `quant job market_data --date <最近交易日>` 观察耗时与额度；快照生成后打开首页检查各模块；历史 4–5 天补齐后复查 52 周与 200 日线指标；结果记入 #70。
