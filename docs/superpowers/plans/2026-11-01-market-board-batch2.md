# 首页市场看板第 2 批（宏观、新闻、财经日历）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页行情条补齐 VIX、美债 10 年、美元、原油、比特币；右栏新闻与财经日历接入真实数据；异动表“原因”列显示关联新闻。

**Architecture:** 新的每日任务 `market_extras`（21:00 ET，在 `market_data` 之后）从 FRED、Coinbase、Finnhub、Forex Factory 取数，各数据源互不影响，分别原子写入 `data_dir/market/{macro,news,calendar}.json`；接口只读这些文件。数据整理为纯函数（`quant/market/macro.py`、`news.py`、`calendar.py`），客户端在 `data/providers/` 下，注入 `Fetch` 以便用固定样本测试。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pandas（不必要时不用）、React 19 + TypeScript、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md`（5.1 行情条 / 新闻 / 财经日历 / 异动“原因”列，7.6，9.1）；第 1 批计划 `docs/superpowers/plans/2026-11-01-market-board-batch1.md`。服务器实测记录见 #60（2026-11-01）。

## Global Constraints

- 面板只读，唯一写操作是提交回测；新接口只有 GET（`tests/test_api_readonly.py`）。
- key 只放服务器 `.env`，不进仓库、日志、异常信息与接口返回。FRED 的 key 只能放在网址参数里，所以 FRED 客户端的任何异常信息都不得包含网址（沿用 massive.py 的 `from None` 写法）。
- 免费数据源的数据不得提交到仓库；测试只用手写的小样本。
- 业务时间一律美东（`America/New_York`）；交易日以 `XNYS` 日历为准。
- DXY 免费源没有：美元用 FRED `DTWEXBGS`（广义美元指数，周频），页面注明“广义美元指数 · 周”。原油注明“WTI”，比特币注明“Coinbase · UTC 日线”。
- FRED 当天的 VIX 等序列次日才更新：每项带自己的数据日期，与快照日期不同时显示“截至 MM-DD”。
- 布局：12 列网格、间距 12px、同排等高、表格不换行；在 1280px 和 1600px 下检查。

## 服务器实测得到的事实（2026-11-01）

- FRED：`/fred/series/observations?series_id=&api_key=&file_type=json&observation_start=`，缺失值为 `"."`；`/fred/series?series_id=` 返回 `last_updated`（如 `"2026-10-02 08:24:33-05"`）。
- Coinbase：`https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400` 返回 `[[unix秒, low, high, open, close, volume], ...]`，新的在前，约 300 根；需要 User-Agent。
- Finnhub（请求头 `X-Finnhub-Token`，每分钟 60 次）：`/news?category=general` 100 条（字段 category、datetime（unix 秒）、headline、id、related（为空）、source、summary、url；来源 CNBC/Reuters）；`/company-news?symbol=&from=&to=` 字段同上，related 为代码；`/calendar/earnings?from=&to=` 返回 `{"earningsCalendar": [{symbol, date, hour("bmo"|"amc"|""), quarter, year, epsEstimate, epsActual, revenueEstimate, revenueActual}]}`，**一次最多 1500 条（5 天就满了），所以按天查**。
- Forex Factory：只有 `https://nfs.faireconomy.media/ff_calendar_thisweek.json`（nextweek / lastweek 404）。字段 title、country、date（带时差的 ISO，如 `2026-11-06T08:30:00-05:00`）、impact（High/Medium/Low/Holiday）、forecast、previous（字符串，如 `"63K"`、`"4.2%"`、`"7.11M"`），没有实际值。周日开始一周。

## 决定（计划作者的判断，执行者照做）

- **新闻用 Finnhub 而不是 Massive**：Massive 每分钟 5 次的额度已被 `market_data` 用满。
- **新闻分类**：市场新闻（`/news?category=general`）→ `macro`（显示标签“宏观”）；持仓与异动股的公司新闻 → `company`；标题匹配财报关键词（`earnings|results|quarter|guidance|EPS|revenue`，不区分大小写）的公司新闻 → `earnings`。“持仓”筛选在前端按账户持仓与新闻标签的交集。
- **异动“原因”**：取快照异动表按 |涨跌| 前 30 只加上持仓，逐只查当天公司新闻；在“上一交易日 16:00 ET 到任务运行时”之间，选标题里出现代码（整词）或公司简称（GICS 名称去掉 Inc./Corp./Co./Ltd./plc/Holdings/Group/Class A 等后的第一个词，至少 3 个字母，整词、不区分大小写）的最新一条；没有就不给（前端显示“—”）。
- **经济数据的实际值**：只为下表这些事件从 FRED 取实际值，其他事件实际值为空（显示“—”）。仅当 FRED 序列的 `last_updated`（换算到美东的日期）不早于事件日期时才算本次公布的值。

  | FF 标题 | FRED 序列 | 计算 | 格式 |
  |---|---|---|---|
  | Non-Farm Employment Change | PAYEMS | 最新 − 前一期 | `{:.0f}K` |
  | Unemployment Rate | UNRATE | 最新 | `{:.1f}%` |
  | Average Hourly Earnings m/m | CES0500000003 | 环比 % | `{:.1f}%` |
  | Unemployment Claims | ICSA | 最新 ÷ 1000 | `{:.0f}K` |
  | JOLTS Job Openings | JTSJOL | 最新 ÷ 1000 | `{:.2f}M` |
  | CPI m/m | CPIAUCSL | 环比 % | `{:.1f}%` |
  | Core CPI m/m | CPILFESL | 环比 % | `{:.1f}%` |
  | Core PCE Price Index m/m | PCEPILFE | 环比 % | `{:.1f}%` |
  | Retail Sales m/m | RSAFS | 环比 % | `{:.1f}%` |

- **经济日历只收美国（country == "USD"）的 High（3 点）与 Medium（2 点）**；Low、Holiday 不收。FF 只给本周，所以把取到的事件累积到 `market/ff_events.json`（以 `title + date` 去重，后取的覆盖先取的，丢弃 14 天前的），“已公布”才能显示上周五的数据。
- **财经日历窗口**：已公布 = 上一交易日 00:00 ET 至任务运行时；接下来 = 任务运行时之后 7 天。FOMC 取 `quant.data.events.load_fomc_dates()`，决议时间 14:00 ET，3 点；接下来的窗口对 FOMC 放宽到 14 天。
- **财报**：按天查 Finnhub，已公布查上一交易日与当天，接下来查之后 7 个日历日（最多 9 次请求）。只收持仓，以及标普 500 成分中市值 ≥ 1000 亿美元的（市值取 `MarketStore.caps()`）。同一天同一时段（盘前 / 盘后 / 未定）合并为一行，持仓股排前、其余按代码排序：标题 `"AAPL · AMZN 财报"`，超过 4 只写 `"AAPL · AMZN · META · MSFT 等 6 只财报"`；有持仓的为 3 点，否则 2 点；已公布行给出持仓股（没有就是第一只）的 EPS 实际 / 预期。
- **周末**：任务只在交易日运行，周五晚到周一晚之间“接下来”看不到下周一、二的经济数据（FF 没有下周数据）。页面在“接下来”下注明“经济数据每周一更新”。
- **限速**：Finnhub 客户端两次请求间隔至少 1.1 秒，FRED 至少 0.6 秒；单次任务 Finnhub 约 1 + 9 + 50 次请求，约 1 分钟。

## Review Focus

1. 某个数据源下线或超时（FF 改格式、Coinbase 被封）：其余区块照常更新，这一块保留旧文件、标出更新时间；任务摘要写明原因，不让整个任务失败。——测试：`test_extras_one_source_failing_keeps_others`（Task 6）。
2. key 无效（Finnhub 401、FRED 400 且提示 api_key）：任务失败告警，但其他区块已经写入。——测试：`test_extras_auth_error_raises_after_others_written`（Task 6）。
3. FF 的时间带时差（-05:00 / -04:00，夏令时切换周）：换算成美东后日期、时间都对。——测试：`test_ff_parse_converts_offset_to_et`（Task 2）。
4. FRED 序列尚未更新（NFP 当天上午之前）：实际值为空，而不是显示上个月的值。——测试：`test_actual_requires_fred_updated_on_event_day`（Task 5）。
5. 没有任何持仓、快照不存在：新闻只有宏观，原因列为空，不报错。——测试：`test_news_without_holdings_or_snapshot`（Task 6）。

---

### Task 1: FRED 与 Coinbase 客户端

**Files:**
- Create: `src/quant/data/providers/fred.py`
- Create: `src/quant/data/providers/coinbase.py`
- Test: `tests/test_fred.py`、`tests/test_coinbase.py`

**Interfaces:**
- Consumes: `quant.data.providers.http.Fetch, http_fetch`；`DataUnavailableError`
- Produces:
  - `class FredAuthError(DataUnavailableError)`
  - `@dataclass(frozen=True) class Obs: date: date; value: float`
  - `class FredClient(api_key: SecretStr, fetch: Fetch = http_fetch, *, sleep=time.sleep, clock=time.monotonic, min_interval: float = 0.6)`
    - `observations(series: str, start: date) -> list[Obs]`（升序，跳过 `"."`）
    - `last_updated(series: str) -> datetime`（带时区）
  - `coinbase.btc_daily(fetch: Fetch = http_fetch) -> list[Bar]`，`@dataclass(frozen=True) class Bar: date: date; open: float; high: float; low: float; close: float; volume: float`（升序，日期为 UTC）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fred.py
def test_observations_parses_and_skips_missing():
    body = {"observations": [{"date": "2026-10-28", "value": "17.1"}, {"date": "2026-10-29", "value": "."},
                             {"date": "2026-10-30", "value": "17.2"}]}
    fetch = FakeFetch((200, body))
    got = FredClient(SecretStr("k"), fetch, sleep=lambda s: None).observations("VIXCLS", date(2026, 8, 1))
    assert got == [Obs(date(2026, 10, 28), 17.1), Obs(date(2026, 10, 30), 17.2)]
    assert "series_id=VIXCLS" in fetch.calls[0][0] and "observation_start=2026-08-01" in fetch.calls[0][0]

def test_last_updated_parses_offset():
    body = {"seriess": [{"last_updated": "2026-10-02 08:24:33-05"}]}
    got = FredClient(SecretStr("k"), FakeFetch((200, body)), sleep=lambda s: None).last_updated("PAYEMS")
    assert got == datetime(2026, 10, 2, 13, 24, 33, tzinfo=UTC)

def test_bad_key_is_auth_error_without_key_in_message():
    body = {"error_code": 400, "error_message": "Bad Request.  The value for variable api_key is not registered."}
    with pytest.raises(FredAuthError) as e:
        FredClient(SecretStr("SECRET"), FakeFetch((400, body)), sleep=lambda s: None).observations("X", date(2026, 1, 1))
    assert "SECRET" not in str(e.value) and "api_key=" not in str(e.value)

def test_network_error_message_has_no_url():
    def boom(url, headers): raise OSError(f"failed {url}")
    with pytest.raises(DataUnavailableError) as e:
        FredClient(SecretStr("SECRET"), boom, sleep=lambda s: None).observations("X", date(2026, 1, 1))
    assert "SECRET" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__

# tests/test_coinbase.py
def test_btc_daily_ascending_utc():
    rows = [[1793491200, 77370, 78125.91, 77423.98, 77756.72, 1158.7],
            [1793404800, 76954.57, 77645.12, 77180.42, 77423.98, 2434.2]]
    got = btc_daily(FakeFetch((200, rows)))
    assert [b.date for b in got] == [date(2026, 10, 31), date(2026, 11, 1)]
    assert got[-1].close == 77756.72 and got[-1].open == 77423.98

def test_btc_daily_error():
    with pytest.raises(DataUnavailableError):
        btc_daily(FakeFetch((503, "down")))
```

（`FakeFetch` 与 `tests/test_massive.py` 中的一样：按顺序返回给定的 `(status, body)`，记录 `calls`。抽到 `tests/fakes.py` 共用，test_massive.py 改为从那里导入。）

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest -q tests/test_fred.py tests/test_coinbase.py`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

FRED 基址 `https://api.stlouisfed.org/fred`，参数 `file_type=json`；限速写法同 `MassiveClient._get`。状态 400 且 `error_message` 含 `api_key` → `FredAuthError("FRED 拒绝访问，请检查 FRED_API_KEY")`；429 → `DataUnavailableError`；其他非 200 → `DataUnavailableError(f"FRED {series} 返回 {status}")`。`last_updated` 的 `-05` 补成 `-05:00` 再解析。Coinbase 请求头 `{"User-Agent": "quant-dashboard"}`，非 200 或不是列表 → `DataUnavailableError`。

- [ ] **Step 4: 运行，确认通过**

Run: `uv run pytest -q tests/test_fred.py tests/test_coinbase.py tests/test_massive.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/quant/data/providers/fred.py src/quant/data/providers/coinbase.py tests/
git commit -m "feat: FRED 与 Coinbase 数据客户端（Refs #69）"
```

### Task 2: Finnhub 与 Forex Factory 客户端

**Files:**
- Create: `src/quant/data/providers/finnhub.py`、`src/quant/data/providers/forexfactory.py`
- Test: `tests/test_finnhub.py`、`tests/test_forexfactory.py`

**Interfaces:**
- Produces:
  - `class FinnhubAuthError(DataUnavailableError)`
  - `@dataclass(frozen=True) class NewsRaw: id: str; time: datetime  # UTC; headline: str; source: str; url: str; related: tuple[str, ...]`
  - `@dataclass(frozen=True) class EarningsRaw: symbol: str; date: date; hour: str  # "bmo"|"amc"|""; eps_estimate: float | None; eps_actual: float | None`
  - `class FinnhubClient(api_key: SecretStr, fetch: Fetch = http_fetch, *, sleep=..., clock=..., min_interval: float = 1.1)`
    - `market_news() -> list[NewsRaw]`
    - `company_news(symbol: str, start: date, end: date) -> list[NewsRaw]`
    - `earnings(day: date) -> list[EarningsRaw]`
  - `@dataclass(frozen=True) class FfEvent: title: str; time: datetime  # America/New_York; impact: int  # 3/2/1, Holiday=0; forecast: str; previous: str`
  - `forexfactory.this_week(fetch: Fetch = http_fetch) -> list[FfEvent]`（只返回 country == "USD"）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_finnhub.py
def test_market_news_parses():
    body = [{"category": "business", "datetime": 1793526131, "headline": "OPEC+ steady", "id": 8705821,
             "related": "", "source": "Reuters", "summary": "x", "url": "https://n/1"}]
    fetch = FakeFetch((200, body))
    got = client(fetch).market_news()
    assert fetch.calls[0][0] == "https://finnhub.io/api/v1/news?category=general"
    assert fetch.calls[0][1]["X-Finnhub-Token"] == "k"
    assert got == [NewsRaw("8705821", datetime.fromtimestamp(1793526131, UTC), "OPEC+ steady", "Reuters", "https://n/1", ())]

def test_company_news_url_and_related():
    body = [{"datetime": "1793482766", "headline": "h", "id": 1, "related": "AAPL,MSFT", "source": "Yahoo", "url": "u"}]
    fetch = FakeFetch((200, body))
    got = client(fetch).company_news("BRK.B", date(2026, 10, 29), date(2026, 10, 30))
    assert fetch.calls[0][0] == "https://finnhub.io/api/v1/company-news?symbol=BRK.B&from=2026-10-29&to=2026-10-30"
    assert got[0].related == ("AAPL", "MSFT")

def test_earnings_day():
    body = {"earningsCalendar": [{"symbol": "XOM", "date": "2026-10-30", "hour": "bmo", "epsEstimate": 1.88,
                                  "epsActual": 1.95, "quarter": 3, "year": 2026}]}
    fetch = FakeFetch((200, body))
    got = client(fetch).earnings(date(2026, 10, 30))
    assert "from=2026-10-30&to=2026-10-30" in fetch.calls[0][0]
    assert got == [EarningsRaw("XOM", date(2026, 10, 30), "bmo", 1.88, 1.95)]

def test_401_auth_403_unavailable():
    with pytest.raises(FinnhubAuthError):
        client(FakeFetch((401, {"error": "Invalid API key"}))).market_news()
    with pytest.raises(DataUnavailableError) as e:
        client(FakeFetch((403, {"error": "premium"}))).market_news()
    assert not isinstance(e.value, FinnhubAuthError)

def test_malformed_items_skipped():
    body = [{"headline": "no time"}, {"datetime": 1793526131, "headline": "ok", "id": 2, "source": "CNBC", "url": "u"}]
    assert [n.headline for n in client(FakeFetch((200, body))).market_news()] == ["ok"]

# tests/test_forexfactory.py
def test_ff_parse_converts_offset_to_et():
    body = [{"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00",
             "impact": "High", "forecast": "63K", "previous": "29K"},
            {"title": "Daylight Saving Time Shift", "country": "USD", "date": "2026-11-01T02:00:00-04:00",
             "impact": "Holiday", "forecast": "", "previous": ""},
            {"title": "German Prelim CPI", "country": "EUR", "date": "2026-11-02T08:00:00-05:00",
             "impact": "High", "forecast": "", "previous": ""}]
    got = this_week(FakeFetch((200, body)))
    assert [e.title for e in got] == ["Non-Farm Employment Change", "Daylight Saving Time Shift"]
    assert got[0].time == datetime(2026, 11, 6, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    assert got[0].impact == 3 and got[1].impact == 0 and got[1].time.hour == 2

def test_ff_not_json_is_unavailable():
    with pytest.raises(DataUnavailableError):
        this_week(FakeFetch((200, "<html>")))
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest -q tests/test_finnhub.py tests/test_forexfactory.py`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

Finnhub 基址 `https://finnhub.io/api/v1`；状态码处理与 massive.py 相同（401 → Auth，403 → 普通 DataUnavailableError，429 → DataUnavailableError），异常信息不含 key。`datetime` 可能是整数或字符串；缺 headline / datetime 的条目跳过；`related` 按逗号拆开、去空。FF 请求头 `{"User-Agent": "Mozilla/5.0"}`，impact 映射 `{"High": 3, "Medium": 2, "Low": 1}`，其他为 0；时间用 `datetime.fromisoformat(...).astimezone(ET)`。

- [ ] **Step 4: 运行，确认通过**

Run: `uv run pytest -q tests/test_finnhub.py tests/test_forexfactory.py`
Expected: PASS

- [ ] **Step 5: 提交** — `feat: Finnhub 与 Forex Factory 数据客户端（Refs #69）`

### Task 3: 行情条宏观项（纯计算）

**Files:**
- Create: `src/quant/market/macro.py`
- Test: `tests/test_market_macro.py`

**Interfaces:**
- Consumes: `Obs`（Task 1）、`Bar`（Task 1）
- Produces:
  - `MACRO_SERIES: dict[str, tuple[str, str, str]]` = `{"VIX": ("VIXCLS", "VIX", ""), "US10Y": ("DGS10", "美债 10 年", ""), "USD": ("DTWEXBGS", "美元指数", "广义美元指数 · 周"), "WTI": ("DCOILWTICO", "原油", "WTI")}`（键 → FRED 序列、标签、注明）
  - `@dataclass(frozen=True) class MacroItem: symbol: str; label: str; close: float | None; change: float | None; change_pct: float | None; spark: list[float]; as_of: str | None; note: str; unit: str  # "price" | "yield"`
  - `macro_item(symbol: str, label: str, note: str, points: list[tuple[date, float]], unit: str = "price") -> MacroItem`
  - `build_macro(fred: Mapping[str, list[Obs]], btc: list[Bar] | None) -> list[MacroItem]`（顺序 VIX、US10Y、USD、WTI、BTC；缺失的源不出现在列表里）

- [ ] **Step 1: 写失败测试**

```python
def test_macro_item_change_and_spark():
    pts = [(date(2026, 9, 1) + timedelta(days=i), 100.0 + i) for i in range(40)]
    it = macro_item("WTI", "原油", "WTI", pts)
    assert it.close == 139.0 and it.change == 1.0 and it.change_pct == pytest.approx(1 / 138)
    assert len(it.spark) == 30 and it.spark[-1] == 139.0 and it.as_of == "2026-10-10"

def test_yield_has_no_pct():
    it = macro_item("US10Y", "美债 10 年", "", [(date(2026, 10, 28), 4.93), (date(2026, 10, 29), 4.98)], unit="yield")
    assert it.change == pytest.approx(0.05) and it.change_pct is None and it.unit == "yield"

def test_single_point_has_no_change():
    it = macro_item("VIX", "VIX", "", [(date(2026, 10, 29), 17.2)])
    assert it.close == 17.2 and it.change is None and it.change_pct is None

def test_build_macro_order_and_missing():
    fred = {"VIXCLS": [Obs(date(2026, 10, 28), 17.0), Obs(date(2026, 10, 29), 17.2)],
            "DGS10": [Obs(date(2026, 10, 29), 4.98)]}
    btc = [Bar(date(2026, 10, 30), 1, 1, 1, 77000.0, 1), Bar(date(2026, 10, 31), 1, 1, 1, 77756.72, 1)]
    items = build_macro(fred, btc)
    assert [i.symbol for i in items] == ["VIX", "US10Y", "BTC"]
    assert items[1].unit == "yield" and items[2].note == "Coinbase · UTC 日线" and items[2].label == "比特币"
```

- [ ] **Step 2: 运行，确认失败** — `uv run pytest -q tests/test_market_macro.py` → FAIL（ModuleNotFoundError）
- [ ] **Step 3: 实现**（spark 取最近 30 个点；US10Y 的 unit 为 `"yield"`，其余 `"price"`；空序列视为缺失）
- [ ] **Step 4: 运行，确认通过** — 同上 → PASS
- [ ] **Step 5: 提交** — `feat: 行情条宏观项计算（Refs #70）`

### Task 4: 新闻流与异动原因（纯计算）

**Files:**
- Create: `src/quant/market/news.py`
- Test: `tests/test_market_news.py`

**Interfaces:**
- Consumes: `NewsRaw`（Task 2）
- Produces:
  - `EARNINGS_RE = re.compile(r"\b(earnings|results|quarter|guidance|eps|revenue)\b", re.I)`
  - `@dataclass(frozen=True) class NewsItem: time: str  # UTC ISO; headline: str; source: str; url: str; tags: list[str]; category: str  # "macro"|"company"|"earnings"`
  - `@dataclass(frozen=True) class Reason: headline: str; source: str; time: str; url: str`
  - `short_name(name: str | None) -> str | None`
  - `pick_reason(symbol: str, name: str | None, items: list[NewsRaw], since: datetime) -> Reason | None`
  - `build_feed(market: list[NewsRaw], company: Mapping[str, list[NewsRaw]], holdings: set[str], since: datetime, limit: int = 80) -> list[NewsItem]`

- [ ] **Step 1: 写失败测试**

```python
T0 = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)  # 美东 16:00
def n(h, mins=0, i=None, rel=()):  # 构造 NewsRaw
    return NewsRaw(i or h, T0 + timedelta(minutes=mins), h, "Reuters", f"https://n/{i or h}", tuple(rel))

def test_short_name():
    assert short_name("Apple Inc.") == "Apple"
    assert short_name("Exxon Mobil Corp.") == "Exxon"
    assert short_name("3M") is None and short_name(None) is None

def test_pick_reason_requires_symbol_or_name_and_window():
    items = [n("5 Best High Growth Stocks", 30), n("Apple beats on iPhone sales", 10),
             n("AAPL old news", -24 * 60), n("Pineapple prices jump", 40)]
    r = pick_reason("AAPL", "Apple Inc.", items, since=T0 - timedelta(hours=20))
    assert r is not None and r.headline == "Apple beats on iPhone sales"
    assert pick_reason("AAPL", "Apple Inc.", items[:1], since=T0 - timedelta(hours=20)) is None

def test_pick_reason_latest_wins():
    items = [n("NVDA rises", 5), n("Nvidia extends gains", 50)]
    assert pick_reason("NVDA", "Nvidia", items, since=T0).headline == "Nvidia extends gains"

def test_feed_categories_and_tags():
    market = [n("Fed holds rates", 1, "m1")]
    company = {"XOM": [n("Exxon Q3 earnings beat", 2, "c1"), n("Exxon names CFO", 3, "c2")],
               "NVDA": [n("Nvidia rallies", 4, "c3")]}
    feed = build_feed(market, company, holdings={"XOM"}, since=T0 - timedelta(days=2))
    by = {f.headline: f for f in feed}
    assert by["Fed holds rates"].category == "macro" and by["Fed holds rates"].tags == []
    assert by["Exxon Q3 earnings beat"].category == "earnings" and by["Exxon Q3 earnings beat"].tags == ["XOM"]
    assert by["Exxon names CFO"].category == "company"
    assert "Nvidia rallies" not in by  # 非持仓的公司新闻只用于原因列
    assert [f.time for f in feed] == sorted((f.time for f in feed), reverse=True)

def test_feed_dedup_and_limit():
    market = [n("Same", 1, "a"), n("Same", 2, "b")] + [n(f"h{i}", i + 10, f"x{i}") for i in range(100)]
    feed = build_feed(market, {}, set(), since=T0 - timedelta(days=2), limit=80)
    assert len(feed) == 80 and sum(f.headline == "Same" for f in feed) <= 1
    assert feed[0].time > feed[-1].time
```

- [ ] **Step 2: 运行，确认失败** — `uv run pytest -q tests/test_market_news.py` → FAIL
- [ ] **Step 3: 实现**：去掉公司后缀的正则 `\b(inc|corp|corporation|co|company|ltd|plc|holdings|group|class [a-z])\b\.?`（不区分大小写），取第一个词，去掉非字母，少于 3 个字母返回 None。匹配用整词、不区分大小写。feed 去重键为小写标题；`time` 早于 `since` 的丢掉；持仓公司新闻每只最多 5 条。
- [ ] **Step 4: 运行，确认通过** — 同上 → PASS
- [ ] **Step 5: 提交** — `feat: 新闻流与异动原因计算（Refs #70）`

### Task 5: 财经日历（纯计算）

**Files:**
- Create: `src/quant/market/calendar.py`
- Test: `tests/test_market_calendar.py`

**Interfaces:**
- Consumes: `FfEvent`（Task 2）、`EarningsRaw`（Task 2）、`Obs`（Task 1）
- Produces:
  - `ACTUALS: dict[str, tuple[str, str, str]]`：FF 标题 → (FRED 序列, 计算 `"diff"|"level"|"pct"|"k"|"m"`, 格式串)，取值见本计划“决定”一节的表
  - `fred_actual(title: str, event_day: date, obs: list[Obs], updated: datetime | None) -> str | None`
  - `merge_ff(old: list[dict[str, Any]], new: list[FfEvent], today: date) -> list[dict[str, Any]]`（累积，存盘格式 `{"title", "time"(ET ISO), "impact", "forecast", "previous"}`）
  - `@dataclass(frozen=True) class CalItem: time: str  # ET ISO; date_only: bool; kind: str  # "econ"|"earnings"|"fomc"; title: str; impact: int; actual: str | None; forecast: str | None; previous: str | None; symbols: list[str]; held: bool; session: str | None  # "盘前"|"盘后"|None`
  - `build_calendar(ff: list[dict[str, Any]], actuals: Mapping[str, str | None], earnings: list[EarningsRaw], fomc: Sequence[date], holdings: set[str], big: set[str], now: datetime, prev_day: date) -> tuple[list[CalItem], list[CalItem]]`（已公布按时间降序，接下来按时间升序）。`actuals` 的键为 `f"{title}|{ET 日期}"`。

- [ ] **Step 1: 写失败测试**

```python
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=ET)

def test_actual_requires_fred_updated_on_event_day():
    obs = [Obs(date(2026, 9, 1), 159_000.0), Obs(date(2026, 10, 1), 159_063.0)]
    upd_same = datetime(2026, 11, 6, 13, 40, tzinfo=UTC)
    upd_old = datetime(2026, 10, 2, 13, 24, tzinfo=UTC)
    assert fred_actual("Non-Farm Employment Change", date(2026, 11, 6), obs, upd_same) == "63K"
    assert fred_actual("Non-Farm Employment Change", date(2026, 11, 6), obs, upd_old) is None
    assert fred_actual("ISM Manufacturing PMI", date(2026, 11, 2), obs, upd_same) is None

def test_actual_formats():
    upd = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)
    assert fred_actual("Unemployment Rate", date(2026, 11, 13), [Obs(date(2026, 10, 1), 4.2)], upd) == "4.2%"
    cpi = [Obs(date(2026, 9, 1), 320.0), Obs(date(2026, 10, 1), 320.96)]
    assert fred_actual("CPI m/m", date(2026, 11, 13), cpi, upd) == "0.3%"
    assert fred_actual("Unemployment Claims", date(2026, 11, 13), [Obs(date(2026, 11, 8), 197_000.0)], upd) == "197K"
    assert fred_actual("JOLTS Job Openings", date(2026, 11, 13), [Obs(date(2026, 9, 1), 7110.0)], upd) == "7.11M"

def test_merge_ff_accumulates_and_prunes():
    ev = lambda t, d, f="1": FfEvent(t, datetime.fromisoformat(d).astimezone(ET), 3, f, "0")
    old = merge_ff([], [ev("A", "2026-10-15T08:30:00-04:00"), ev("B", "2026-10-30T08:30:00-04:00", "1")], date(2026, 10, 30))
    new = merge_ff(old, [ev("B", "2026-10-30T08:30:00-04:00", "2"), ev("C", "2026-11-02T10:00:00-05:00")], date(2026, 11, 2))
    assert [(e["title"], e["forecast"]) for e in new] == [("B", "2"), ("C", "1")]  # A 已超过 14 天

def test_calendar_split_and_grouping():
    ff = [{"title": "Non-Farm Employment Change", "time": "2026-11-06T08:30:00-05:00", "impact": 3, "forecast": "63K", "previous": "29K"},
          {"title": "ISM Services PMI", "time": "2026-11-09T10:00:00-05:00", "impact": 2, "forecast": "54.9", "previous": "54.9"},
          {"title": "Old", "time": "2026-11-04T10:00:00-05:00", "impact": 2, "forecast": "", "previous": ""}]
    earn = [EarningsRaw("XOM", date(2026, 11, 6), "bmo", 1.88, 1.95), EarningsRaw("CVX", date(2026, 11, 6), "bmo", 2.43, 2.5),
            EarningsRaw("AAPL", date(2026, 11, 10), "amc", 1.6, None), EarningsRaw("MSFT", date(2026, 11, 10), "amc", 3.1, None),
            EarningsRaw("ZZZ", date(2026, 11, 10), "amc", 0.1, None)]
    released, upcoming = build_calendar(
        ff, {"Non-Farm Employment Change|2026-11-06": "63K"}, earn, [date(2026, 12, 9), date(2026, 11, 18)],
        holdings={"CVX"}, big={"XOM", "AAPL", "MSFT"}, now=NOW, prev_day=date(2026, 11, 5))
    r = {i.title: i for i in released}
    assert set(r) == {"Non-Farm Employment Change", "CVX · XOM 财报"}  # “Old” 早于上一交易日；持仓股排前
    assert r["Non-Farm Employment Change"].actual == "63K"
    eq = r["CVX · XOM 财报"]
    assert eq.held and eq.impact == 3 and eq.session == "盘前" and eq.actual == "2.5" and eq.forecast == "2.43"
    u = [i.title for i in upcoming]
    assert u == ["ISM Services PMI", "AAPL · MSFT 财报", "FOMC 利率决议"]  # ZZZ 不在持仓与大市值里；12-09 超过 14 天
    fomc = upcoming[-1]
    assert fomc.time.startswith("2026-11-18T14:00") and fomc.impact == 3

def test_calendar_many_earnings_title():
    syms = ["A1", "A2", "A3", "A4", "A5", "A6"]
    earn = [EarningsRaw(s, date(2026, 11, 9), "", None, None) for s in syms]
    _, up = build_calendar([], {}, earn, [], set(), set(syms), NOW, date(2026, 11, 5))
    assert up[0].title == "A1 · A2 · A3 · A4 等 6 只财报" and up[0].session is None and up[0].date_only
```

- [ ] **Step 2: 运行，确认失败** — `uv run pytest -q tests/test_market_calendar.py` → FAIL
- [ ] **Step 3: 实现**：盘前时间记为当天 09:30 ET 之前（排序用 08:00），盘后记为 16:05，未定记为 12:00 且 `date_only=True`；已公布 = `prev_day 00:00 ET ≤ time ≤ now`，接下来 = `now < time ≤ now + 7 天`（FOMC 为 14 天）；财报组内按代码排序，持仓股排前；已公布财报组的 actual/forecast 取组内第一只（持仓优先）的 EPS 实际 / 预期，格式 `f"{v:g}"`。
- [ ] **Step 4: 运行，确认通过** — 同上 → PASS
- [ ] **Step 5: 提交** — `feat: 财经日历计算（经济数据、财报、FOMC）（Refs #70）`

### Task 6: 每日任务 market_extras

**Files:**
- Create: `src/quant/market/extras_job.py`
- Modify: `src/quant/data/market_store.py`（新增 `macro_path`、`news_path`、`calendar_path`、`ff_events_path`）、`src/quant/jobs/daily.py`、`src/quant/jobs/scheduler.py`、`src/quant/core/settings.py`、`configs/base.yaml`、`compose.yml`、`deploy/server_init.md`、`docs/PROJECT_PLAN.md`
- Test: `tests/test_market_extras_job.py`

**Interfaces:**
- Consumes: Task 1–5 全部；`MarketStore`、`snapshot_path`、`load_fomc_dates`、`atomic_write_text`
- Produces:
  - `run_market_extras(*, data_dir: Path, now: datetime, cal: TradingCalendar, holdings: set[str], fred: FredLike | None, finnhub: FinnhubLike | None, fetch: Fetch = http_fetch) -> str`（`FredLike` / `FinnhubLike` 为 Protocol；为 None 表示未配置 key，对应区块跳过）
  - 写出的文件：`market/macro.json` `{"generated_at", "items": [MacroItem...]}`；`market/news.json` `{"generated_at", "as_of"(快照日期或 None), "items": [NewsItem...], "reasons": {symbol: Reason}}`；`market/calendar.json` `{"generated_at", "released": [...], "upcoming": [...]}`
  - 设置：`Settings.fred_api_key: SecretStr | None`、`Settings.finnhub_api_key: SecretStr | None`；`ScheduleSettings.market_extras = "21:00"`；`JOBS`、`JOB_FUNCS`、`day_plan` 增加 `market_extras`
  - compose scheduler 环境：`QUANT_FRED_API_KEY: ${FRED_API_KEY:-}`、`QUANT_FINNHUB_API_KEY: ${FINNHUB_API_KEY:-}`

顺序：宏观（FRED 4 个序列 + Coinbase）→ 新闻（市场新闻；快照异动前 30 + 持仓的公司新闻，范围上一交易日到 now 的日期）→ 日历（FF 合并 → ACTUALS 中本周已到时间的事件取 FRED 观测与 last_updated → 财报按天 → FOMC）。每个区块单独 try：`DataUnavailableError`（非认证类）→ 记入摘要、保留旧文件；认证错误先记下，所有区块跑完后再抛出第一个。宏观区块部分成功也写（缺的项不出现）。FRED 观测起点 `today - 260 天`。

- [ ] **Step 1: 写失败测试**（假 FRED / Finnhub 客户端；`fetch` 假函数按网址返回 Coinbase 与 FF 样本）

```python
def test_extras_writes_three_files(tmp_path): ...
    # 断言 macro.json 的 symbol 顺序 ["VIX","US10Y","USD","WTI","BTC"]；news.json items 非空且 reasons 含快照异动股；
    # calendar.json released/upcoming 存在；摘要含 “宏观 5 项”、“新闻”、“日历”
def test_extras_one_source_failing_keeps_others(tmp_path): ...
    # 先完整跑一次；第二次 FF 返回 "<html>"、Coinbase 返回 503：calendar.json 的经济事件沿用 ff_events.json，
    # macro.json 只缺 BTC；news.json 已更新；摘要含 “Forex Factory” 与 “Coinbase”；不抛异常
def test_extras_auth_error_raises_after_others_written(tmp_path): ...
    # Finnhub 抛 FinnhubAuthError：pytest.raises(FinnhubAuthError)，但 macro.json 已写入
def test_news_without_holdings_or_snapshot(tmp_path): ...
    # 没有 snapshot.json、holdings=set()：news.json 只有 macro 类，reasons == {}，as_of is None
def test_extras_without_keys_skips_fred_and_finnhub(tmp_path): ...
    # fred=None, finnhub=None：macro.json 只有 BTC；没有 news.json；calendar 只有 FF 与 FOMC；摘要含 “未配置”
def test_finnhub_requests_bounded(tmp_path): ...
    # 快照异动 50 只 + 持仓 20 只（10 只重叠）：company_news 调用次数 == 30 + 10，earnings 调用 ≤ 9
def test_scheduler_includes_market_extras():
    plan = dict(day_plan(date(2026, 10, 30), ScheduleSettings(), get_calendar()))
    assert plan["market_extras"] == datetime(2026, 10, 30, 21, 0, tzinfo=ET)
    assert "market_extras" in JOBS and "market_extras" in JOB_FUNCS
def test_job_empty_keys_treated_as_missing(tmp_path): ...
    # Settings 中两个 key 为 SecretStr("")：market_extras(ctx, d) 不构造 FRED / Finnhub 客户端（只跑 Coinbase 与 FF）
```

执行者把每个 `...` 写成完整测试，断言按注释。持仓的取法从 `market_data` 中抽出 `_all_holdings(ctx) -> set[str]`，两个任务共用（`market_data` 原有测试必须继续通过）。

- [ ] **Step 2: 运行，确认失败** — `uv run pytest -q tests/test_market_extras_job.py` → FAIL
- [ ] **Step 3: 实现** `run_market_extras` 与 `daily.market_extras(ctx, d)`（`now = ctx.now()` 换算到美东；key 为空串视同未配置；与交易无关，不碰 `TRADING_MODE`）；设置、调度、compose、`deploy/server_init.md`（新增两行 `.env` 说明与手动运行命令 `quant job market_extras --date <日期>`）、`docs/PROJECT_PLAN.md` 的任务表同步。
- [ ] **Step 4: 运行，确认通过** — `uv run pytest -q tests/test_market_extras_job.py tests/test_market_job.py tests/test_scheduler*.py` → PASS
- [ ] **Step 5: 提交** — `feat: 每日任务 market_extras 生成宏观、新闻、财经日历（Refs #69 #70）`

### Task 7: 接口

**Files:**
- Modify: `src/quant/api/routes/market.py`、`src/quant/api/schemas.py`
- Test: `tests/test_api_market.py`

**Interfaces:**
- Consumes: Task 6 的三个文件与路径函数
- Produces:
  - `TickerItemOut` 增加 `as_of: date | None = None`、`note: str = ""`、`unit: Literal["price", "yield"] = "price"`
  - `MarketOverview` 增加 `macro: list[TickerItemOut] = []`（快照 pending 时也返回）
  - `NewsRefOut {headline, source, time: datetime, url}`；`MoverOut.reason: NewsRefOut | None = None`（仅当 news.json 的 `as_of` 等于快照 `as_of` 时合并）
  - `GET /api/market/news` → `MarketNews {status: "ok"|"pending", generated_at, items: list[NewsItemOut]}`，`NewsItemOut {time: datetime, headline, source, url, tags: list[str], category: Literal["macro","company","earnings"]}`
  - `GET /api/market/calendar` → `MarketCalendar {status, generated_at, released: list[CalItemOut], upcoming: list[CalItemOut]}`，`CalItemOut` 字段同 `CalItem`（`time: datetime`）

- [ ] **Step 1: 写失败测试**

```python
def test_overview_includes_macro_even_when_pending(client, data_dir): ...
    # 只有 macro.json：status == "pending"，macro[0]["symbol"] == "VIX"，macro[1]["unit"] == "yield"
def test_mover_reason_merged_only_for_same_day(client, data_dir): ...
    # snapshot as_of 2026-10-30 + news as_of 2026-10-30 → reason 合并；news as_of 2026-10-29 → reason is None
def test_news_and_calendar_endpoints(client, data_dir): ...
    # 正常返回；文件缺失或损坏 → {"status": "pending"}，状态码 200
```

- [ ] **Step 2: 运行，确认失败** — `uv run pytest -q tests/test_api_market.py` → FAIL
- [ ] **Step 3: 实现**；读取失败（文件缺失、JSON 损坏、字段校验失败）一律当 pending，不返回 500。
- [ ] **Step 4: 运行，确认通过** — `uv run pytest -q tests/test_api_market.py tests/test_api_readonly.py` → PASS；然后 `make api-types`
- [ ] **Step 5: 提交** — `feat: 首页宏观、新闻、财经日历接口（Refs #70）`

### Task 8: 前端

**Files:**
- Modify: `web/src/pages/MarketPage.tsx`、`web/src/styles/layout.css`（或首页样式所在文件）
- Create: `web/src/components/NewsPanel.tsx`、`web/src/components/CalendarPanel.tsx`
- Test: `web/src/pages/MarketPage.test.tsx`

**Interfaces:**
- Consumes: Task 7 的接口与生成的 `web/src/api/types.ts`
- Produces: 页面行为如下

行情条顺序固定为 SPY、QQQ、DIA、IWM、VIX、US10Y、USD、WTI、GLD、BTC；没有数据的项仍显示“数据接入中”。宏观项不链接到个股页；收益率项涨跌显示为基点（`+5bp`），不显示百分比；`note` 用小字显示在标签后；`as_of` 与快照日期不同时显示“截至 MM-DD”。新闻面板：时间（美东 HH:MM，非今天的显示 MM-DD）、标签（宏观 / 代码）、来源、标题（新窗口打开链接，`rel="noreferrer"`），筛选“全部 / 宏观 / 持仓 / 财报”（持仓 = 标签与当前账户持仓有交集）。日历面板：“已公布”行显示时间、标题、实际（没有为“—”）、预期 · 前值；“接下来”行显示日期时间（财报为“盘前 / 盘后 / 时间未定”）、标题、预期 · 前值、重要性圆点（3 个圆点，亮 impact 个）；“接下来”底部小字“经济数据每周一更新”。异动表在“信号”后加“原因（关联新闻）”列：`标题 · 来源 HH:MM`，单行省略，`title` 属性给全文；没有为“—”。

- [ ] **Step 1: 写失败测试**

```ts
it("strip shows macro items in order with yield in bp", ...)        // 10 格顺序；US10Y 显示 "+5bp"；VIX 显示 "截至 10-29"；剩余 "数据接入中" 0 个
it("news filters", ...)                                              // 全部 3 条；宏观 1 条；持仓（holdings 含 XOM）1 条；财报 1 条
it("calendar released and upcoming", ...)                            // “实际 63K”；“盘前”；3 个圆点中 2 个亮（用 data-on 计数）；“经济数据每周一更新”
it("mover reason column", ...)                                       // NVDA 行显示 "Nvidia extends gains · Reuters"；XOM 行显示 "—"
it("news and calendar pending", ...)                                 // 两个接口返回 pending：显示 “数据接入中”
```

- [ ] **Step 2: 运行，确认失败** — `cd web && npx vitest run src/pages/MarketPage.test.tsx` → FAIL
- [ ] **Step 3: 实现**（组件文件只导出组件，满足 oxlint `only-export-components`；时间格式化用 `Intl.DateTimeFormat("zh-CN", { timeZone: "America/New_York", ... })`）
- [ ] **Step 4: 运行，确认通过** — `make web-check` → PASS
- [ ] **Step 5: 提交** — `feat: 首页行情条宏观项、新闻、财经日历与异动原因（Refs #70）`

### Task 9: 联调与验收

**Files:**
- Modify: `docs/superpowers/plans/2026-11-01-market-board-batch2.md`（勾选）

- [ ] **Step 1**：`make check` 全部通过（后端带 `QUANT_TEST_DATABASE_URL` 跑数据库测试）。
- [ ] **Step 2**：用合成的四个 JSON 文件启动面板（不连外部数据源、不写开发库），在 1280px 与 1600px 截图检查：右栏三块等高对齐、新闻与日历不换行溢出、行情条 10 格宽度一致、异动表原因列省略正常。
- [ ] **Step 3**：部署后在服务器 scheduler 容器内运行 `quant job market_extras --date <最近交易日>`，记录耗时、各区块条数、FRED 实际值是否正确（对照 FF 前值 / 官方公布），结果写入 #70。
- [ ] **Step 4**：提交（如有修改）。
