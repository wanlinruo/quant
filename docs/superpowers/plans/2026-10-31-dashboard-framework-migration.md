# 面板框架迁移（FastAPI + React）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 FastAPI 只读接口 + React 单页应用替换 Streamlit 面板，功能不少于现在，并套用新设计的外框、导航、账户切换和布局规范。

**Architecture:** 后端 `src/quant/api/`：把 `src/quant/dashboard/queries.py` 改写成不依赖 Streamlit 的纯查询函数，返回 Pydantic 模型；FastAPI 提供 `/api/*` 只读接口和唯一的 `POST /api/backtests`，同时托管前端打包产物并回落到 `index.html`。前端 `web/`：Vite + React + TypeScript，接口类型由 OpenAPI 自动生成。仍是一个镜像、一个 `dashboard` 容器、端口 8501，Caddy 不改。

**Tech Stack:** Python 3.12、FastAPI、uvicorn、SQLAlchemy 2、Pydantic 2、pytest（`fastapi.testclient`，需 `httpx`）；Node 24 LTS、npm、Vite、React 19、TypeScript、react-router、lightweight-charts、openapi-typescript、Vitest + Testing Library、ESLint。

**Spec:** `docs/superpowers/specs/2026-10-31-dashboard-redesign-design.md`（本计划只实现其第 8 节第 1 步）；原型 `docs/superpowers/specs/2026-10-31-dashboard-redesign-prototype.html`。

## Global Constraints

- 面板唯一的写操作是提交回测：路由表中除 `POST /api/backtests` 外不得有 POST / PUT / PATCH / DELETE。
- `src/quant/api/` 不得导入 `quant.broker`、`tigeropen`、`quant.core.safety`、`quant.engine.executor`，不得出现 `place_order`、`cancel_order`、`plan_orders`、`check_order_allowed`（沿用 `tests/test_dashboard.py` 的规则）。
- 接口返回不得包含 `database_url`、密钥、账户号、webhook；`/api/meta` 只返回 `trading_mode`、`broker`、是否配置数据库、各账户是否有数据、数据截至时间。
- 账户模式取值 `sim | paper | live`；页面文案：`sim`=“本地模拟”、`paper`=“模拟盘”、`live`=“实盘”。账户选择保存在网址参数 `?account=`。
- 实盘视觉：页面顶部 3px 橙色细条 `#f97316`，账户相关面板标题带“实盘”；橙色不用于涨跌。
- 涨跌颜色：绿涨 `#22c55e`、红跌 `#ef4444`；主色 `#2962ff`；背景 `#0c0e15`，面板 `#11141c`，边框 `#232733`。
- 布局：12 列网格；面板横纵间距一律 12px；同排面板等高；表格单元格 `white-space: nowrap`，表头与数据同侧对齐，长表在面板内滚动且表头固定。
- 时间：业务时间按美东时间显示，顶栏同时显示北京时间。
- 缺数据显示“暂无数据”，未接入的数据源显示“数据接入中”，不得显示示例数据。
- 本地与 CI 测试 `TRADING_MODE=sim`；数据库测试沿用 `QUANT_TEST_DATABASE_URL` 与 `@pytest.mark.db`。
- 前端代码不得包含任何密钥（仓库公开）。

## Review Focus

- 未配置数据库：所有依赖数据库的接口返回 503 和 `{"detail": "未配置数据库（QUANT_DATABASE_URL）"}`，前端显示该提示而不是白屏。→ Task 2 测试 `test_endpoints_without_db_return_503`；Task 5 测试 `shows database warning on 503`。
- 账户没有任何数据（如刚上线的 live）：接口返回空列表，前端显示“暂无数据”，账户按钮置灰。→ Task 1 测试 `test_accounts_reports_has_data`；Task 4 测试 `disables accounts without data`。
- 非法账户参数（`/api/portfolio/foo/...` 或 `?account=foo`）：接口 422；前端回落到默认账户。→ Task 2 测试 `test_invalid_mode_is_422`；Task 4 测试 `falls back on unknown account`。
- 在前端子路由上刷新（`/backtest/128`、`/stock/NVDA`）返回 `index.html`；而 `/api/不存在` 返回 JSON 404，不能被回落吞掉。→ Task 3 测试 `test_spa_fallback_and_api_404`。
- 回测提交参数非法：返回 422 和中文错误列表，数据库不新增行。→ Task 2 测试 `test_submit_invalid_does_not_insert`。

---

## 文件结构

```
src/quant/api/
  __init__.py
  schemas.py        # Pydantic 响应/请求模型（接口契约，前端类型由此生成）
  queries.py        # 纯查询：Session/ParquetStore 进，模型出；无 Streamlit
  backtests.py      # 回测提交的校验与写入（从 dashboard/pages/backtest.py 迁出）
  deps.py           # 依赖注入：settings、engine、session、store
  routes/__init__.py
  routes/meta.py
  routes/portfolio.py
  routes/backtests.py
  routes/system.py
  routes/market.py
  app.py            # create_app()：挂路由、静态文件与 SPA 回落
  openapi.py        # python -m quant.api.openapi → 输出 OpenAPI JSON
tests/
  api_fixtures.py   # 数据库夹具与示例数据（从 test_dashboard_pages.py 迁出）
  test_api_queries.py
  test_api_routes.py
  test_api_app.py
  test_api_readonly.py
web/
  package.json  package-lock.json  tsconfig.json  vite.config.ts  eslint.config.js  index.html
  src/main.tsx  src/App.tsx
  src/api/openapi.json   # 生成，提交入库
  src/api/types.ts       # openapi-typescript 生成，提交入库
  src/api/client.ts
  src/styles/tokens.css  src/styles/layout.css
  src/hooks/useAccount.ts
  src/components/TopBar.tsx  AccountSwitch.tsx  Panel.tsx  Grid.tsx  DataTable.tsx  EmptyState.tsx  Stat.tsx
  src/charts/LineChart.tsx  src/charts/CandleChart.tsx
  src/pages/MarketPage.tsx  PortfolioPage.tsx  BacktestPage.tsx  BacktestSubmitDrawer.tsx  SystemPage.tsx  StockPage.tsx
  src/**/*.test.tsx
删除：src/quant/dashboard/、tests/test_dashboard*.py
修改：pyproject.toml、uv.lock、Dockerfile、compose.yml、compose.dev.yml、Makefile、.github/workflows/ci.yml、docs/PROJECT_PLAN.md、CLAUDE.md、.gitignore
```

---

### Task 1: 接口契约与纯查询层

**Files:**
- Create: `src/quant/api/__init__.py`、`src/quant/api/schemas.py`、`src/quant/api/queries.py`、`tests/api_fixtures.py`、`tests/test_api_queries.py`
- Modify: `pyproject.toml`（加 `fastapi`、`uvicorn[standard]`；dev 加 `httpx`），`uv.lock`

**Interfaces:**
- Produces（`schemas.py`，全部 `pydantic.BaseModel`）：
  - `Mode = Literal["sim", "paper", "live"]`；`MODE_LABELS: dict[Mode, str] = {"sim": "本地模拟", "paper": "模拟盘", "live": "实盘"}`
  - `AccountInfo(mode: Mode, label: str, has_data: bool)`
  - `Meta(trading_mode: Mode, broker: str, database_configured: bool, accounts: list[AccountInfo], default_account: Mode, data_as_of: datetime | None)`；`accounts` 固定按 sim、paper、live 顺序，三项都在
  - `Position(snapshot_date: date, symbol: str, qty: int, avg_cost: float, market_value: float)`
  - `AccountPoint(date: date, cash: float, net_liquidation: float)`
  - `SignalOut(strategy: str, signal_date: date, data_as_of: date, target_weights: dict[str, float])`
  - `OrderOut(created_at: datetime, client_order_id: str, symbol: str, side: str, order_type: str, qty: int, limit_price: float | None, reference_price: float | None, status: str, filled_qty: int, avg_fill_price: float | None, reason: str)`
  - `FillOut(filled_at: datetime, symbol: str, side: str, qty: int, price: float, commission: float)`
  - `BacktestRunOut(id: int, strategy: str, status: str, start: date, end: date, params: dict[str, Any], costs: dict[str, str] | None, metrics: dict[str, Any], error: str, created_at: datetime, finished_at: datetime | None)`
  - `EquityPoint(run_id: int, date: date, equity: float, drawdown: float)`
  - `JobStatusOut(job_name: str, trade_date: date, status: str, started_at: datetime, finished_at: datetime | None, error: str)`
  - `FailureOut(time: datetime, source: str, message: str)`
  - `QueueStatus(queued: int = 0, running: int = 0, done: int = 0, failed: int = 0)`
  - `DataStatusRow(symbol: str, last_date: date | None, rows: int, sources: str, last_ingested: datetime | None, ok: bool, issues: str)`
  - `Bar(date: date, open: float, high: float, low: float, close: float, adj_close: float, volume: float)`
  - `CostsIn(commission_per_share: Decimal | None, min_commission: Decimal | None, slippage: Decimal)`、`BacktestSubmit(strategy: str, start: date, end: date, universe: list[str], safe_asset: str, lookback: int, top_k: int, sma_window: int, cash: Decimal, costs: CostsIn)`、`SubmitResult(id: int)`
- Produces（`queries.py`，签名；`s` 为 `sqlalchemy.orm.Session`）：
  - `accounts(s) -> dict[Mode, bool]`（三种 mode 各自在 `account_snapshots` / `position_snapshots` / `orders` 任一表是否有行）
  - `positions(s, mode: Mode) -> list[Position]`（最近一个快照日）
  - `account_curve(s, mode: Mode) -> list[AccountPoint]`
  - `latest_signals(s) -> list[SignalOut]`（每个策略最近一次）
  - `orders(s, mode: Mode, limit: int = 200) -> list[OrderOut]`、`fills(s, mode: Mode, limit: int = 200) -> list[FillOut]`
  - `backtest_runs(s, limit: int = 200) -> list[BacktestRunOut]`（`params` 去掉 `_` 开头的键，`costs` 取 `params["_costs"]`，`error` 只取第一行）
  - `backtest_equity(s, run_ids: Sequence[int]) -> list[EquityPoint]`
  - `job_status(s) -> list[JobStatusOut]`、`recent_failures(s, limit: int = 20) -> list[FailureOut]`、`flags(s) -> dict[str, str]`、`queue_status(s) -> QueueStatus`
  - `last_job_finished(s) -> datetime | None`（`job_runs` 中 `status="success"` 的最大 `finished_at`）
  - `data_status(store: ParquetStore, cal: TradingCalendar, expected: date) -> list[DataStatusRow]`（逻辑同现 `dashboard/queries.py::data_status`，窗口 365 天）
  - `bars(store: ParquetStore, symbol: str, start: date | None) -> list[Bar]`

- [ ] **Step 1: 迁出测试夹具。** 把 `tests/test_dashboard_pages.py` 中建库（alembic upgrade）与写入示例数据的夹具移到 `tests/api_fixtures.py`，导出 `db_url` 夹具（每个测试新库，带 `@pytest.mark.db` 与 `needs_db`）和 `seed(engine, mode: str = "paper") -> None`（写入 2 个账户快照日、2 条持仓、1 条信号、2 条订单、1 条成交、1 条成功 job_run、1 条失败 job_run、1 条 queued 回测 + 1 条 failed 回测）。在 `tests/conftest.py` 用 `pytest_plugins = ["api_fixtures"]` 注册（若已有 conftest 则追加）。

- [ ] **Step 2: 写失败测试 `tests/test_api_queries.py`**

```python
@pytest.mark.db
@needs_db
def test_positions_latest_snapshot(db_url: str) -> None:
    eng = create_engine(db_url); seed(eng, "paper")
    with Session(eng) as s:
        rows = queries.positions(s, "paper")
        assert {r.symbol for r in rows} == {"NVDA", "META"}
        assert all(r.snapshot_date == SEED_LAST_DAY for r in rows)
        assert queries.positions(s, "live") == []

@pytest.mark.db
@needs_db
def test_accounts_reports_has_data(db_url: str) -> None:
    eng = create_engine(db_url); seed(eng, "paper")
    with Session(eng) as s:
        assert queries.accounts(s) == {"sim": False, "paper": True, "live": False}

@pytest.mark.db
@needs_db
def test_backtest_runs_strip_private_params(db_url: str) -> None:
    eng = create_engine(db_url); seed(eng)
    with Session(eng) as s:
        runs = queries.backtest_runs(s)
        assert all(not k.startswith("_") for r in runs for k in r.params)
        failed = next(r for r in runs if r.status == "failed")
        assert "\n" not in failed.error

@pytest.mark.db
@needs_db
def test_queue_and_failures(db_url: str) -> None:
    eng = create_engine(db_url); seed(eng)
    with Session(eng) as s:
        assert queries.queue_status(s) == QueueStatus(queued=1, failed=1)
        sources = [f.source for f in queries.recent_failures(s)]
        assert any(x.startswith("任务 ") for x in sources) and any(x.startswith("回测 #") for x in sources)

def test_data_status_and_bars(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path); write_sample_bars(store, "SPY")   # 两年交易日样本，同 test_dashboard.py::_write_sample
    cal = get_calendar(); expected = cal.last_completed_trading_day(datetime.now(UTC))
    [row] = queries.data_status(store, cal, expected)
    assert row.symbol == "SPY" and row.ok and row.last_date == expected
    assert len(queries.bars(store, "SPY", expected - timedelta(days=30))) > 15
```

`SEED_LAST_DAY`、`write_sample_bars` 在 `tests/api_fixtures.py` 中定义并导出。

- [ ] **Step 3: 运行，确认失败**

Run: `TRADING_MODE=sim uv run pytest tests/test_api_queries.py -v`
Expected: FAIL（`ModuleNotFoundError: quant.api`）

- [ ] **Step 4: 实现 `schemas.py` 与 `queries.py`**，按上面的签名，把 `src/quant/dashboard/queries.py` 中对应函数的查询逻辑搬过来，去掉 `@st.cache_*`，DataFrame 改为模型列表，中文列名改为模型字段名。`uv add fastapi "uvicorn[standard]"`、`uv add --dev httpx`。

- [ ] **Step 5: 运行，确认通过**

Run: `TRADING_MODE=sim QUANT_TEST_DATABASE_URL=<本地测试库> uv run pytest tests/test_api_queries.py -v && uv run mypy`
Expected: PASS；mypy 无错误

- [ ] **Step 6: Commit**

```bash
git add src/quant/api tests/api_fixtures.py tests/conftest.py tests/test_api_queries.py pyproject.toml uv.lock
git commit -m "feat: 面板接口的纯查询层与接口模型（Refs #58）"
```

---

### Task 2: FastAPI 路由与回测提交

**Files:**
- Create: `src/quant/api/deps.py`、`src/quant/api/backtests.py`、`src/quant/api/routes/{__init__,meta,portfolio,backtests,system,market}.py`、`tests/test_api_routes.py`、`tests/test_api_readonly.py`
- Create（先放最小版，Task 3 补静态文件）: `src/quant/api/app.py`

**Interfaces:**
- Consumes: Task 1 的 `schemas`、`queries`。
- Produces（`deps.py`）：`get_settings() -> Settings`（`functools.lru_cache`）、`get_engine() -> Engine | None`（lru_cache，`make_engine(settings.database_url.get_secret_value())`）、`get_session() -> Iterator[Session]`（无引擎时 `HTTPException(503, "未配置数据库（QUANT_DATABASE_URL）")`）、`get_store() -> ParquetStore`。
- Produces（`backtests.py`）：`build_submission(req: BacktestSubmit, configured: CostSettings) -> tuple[dict[str, Any], list[str]]`（行为与现 `dashboard/pages/backtest.py::build_submission` 一致：`MomentumParams.from_mapping` 校验、开始须早于结束、佣金为空报“请填写佣金（老虎实际费率待确认，见 issue #5）”、写入 `COSTS_KEY` 与 `CASH_KEY`）；`submit(s: Session, req: BacktestSubmit, configured: CostSettings) -> int`（有错误时抛 `HTTPException(422, detail=errors)`，否则 `create_run(..., "queued")` 并返回 id）。
- Produces（`app.py`）：`create_app(static_dir: Path | None = None) -> FastAPI`。
- 路由（全部 GET，除注明外）：

| 路径 | 返回 |
|---|---|
| `/api/health` | `{"status": "ok"}`（不查库） |
| `/api/meta` | `Meta`（无数据库时 `database_configured=False`、各账户 `has_data=False`；`default_account`：live 有数据→live，否则 paper 有数据→paper，否则 `trading_mode`） |
| `/api/portfolio/{mode}/positions` | `list[Position]` |
| `/api/portfolio/{mode}/curve` | `list[AccountPoint]` |
| `/api/portfolio/{mode}/orders?limit=` | `list[OrderOut]` |
| `/api/portfolio/{mode}/fills?limit=` | `list[FillOut]` |
| `/api/signals/latest` | `list[SignalOut]` |
| `/api/backtests` | `list[BacktestRunOut]` |
| `/api/backtests/equity?ids=1&ids=2` | `list[EquityPoint]`（最多 5 个 id，超过 422） |
| `/api/backtests/defaults` | `{"strategies": list[str], "costs": {...}, "params": {...}, "last_trading_day": date}`（供提交表单默认值，同现表单） |
| **POST** `/api/backtests` | 请求 `BacktestSubmit`，返回 `SubmitResult`，状态码 201 |
| `/api/system/jobs` | `list[JobStatusOut]` |
| `/api/system/failures` | `list[FailureOut]` |
| `/api/system/flags` | `dict[str, str]` |
| `/api/system/queue` | `QueueStatus` |
| `/api/market/data-status` | `list[DataStatusRow]` |
| `/api/market/bars/{symbol}?start=` | `list[Bar]`（symbol 不存在返回空列表） |

`{mode}` 参数类型为 `Mode`，非法值由 FastAPI 返回 422。

- [ ] **Step 1: 写失败测试 `tests/test_api_routes.py`**（`TestClient(create_app())`；夹具 `client_no_db` 删除 `QUANT_DATABASE_URL` 并 `get_settings.cache_clear(); get_engine.cache_clear()`）

```python
def test_health_and_meta_without_db(client_no_db: TestClient) -> None:
    assert client_no_db.get("/api/health").json() == {"status": "ok"}
    meta = client_no_db.get("/api/meta").json()
    assert meta["database_configured"] is False and meta["trading_mode"] == "sim"
    assert "database_url" not in json.dumps(meta)

@pytest.mark.parametrize("path", ["/api/portfolio/paper/positions", "/api/backtests", "/api/system/jobs", "/api/system/queue"])
def test_endpoints_without_db_return_503(client_no_db: TestClient, path: str) -> None:
    r = client_no_db.get(path)
    assert r.status_code == 503 and r.json()["detail"] == "未配置数据库（QUANT_DATABASE_URL）"

def test_invalid_mode_is_422(client_no_db: TestClient) -> None:
    assert client_no_db.get("/api/portfolio/foo/positions").status_code == 422

@pytest.mark.db
@needs_db
def test_portfolio_and_meta_with_data(client_db: TestClient) -> None:
    assert {p["symbol"] for p in client_db.get("/api/portfolio/paper/positions").json()} == {"NVDA", "META"}
    meta = client_db.get("/api/meta").json()
    assert meta["default_account"] == "paper"
    assert [a["has_data"] for a in meta["accounts"]] == [False, True, False]

@pytest.mark.db
@needs_db
def test_submit_backtest_queues_run(client_db: TestClient, db_url: str) -> None:
    body = valid_submit_body()   # api_fixtures：佣金 0.005 / 最低 1 / 滑点 0.0005，区间两年
    r = client_db.post("/api/backtests", json=body)
    assert r.status_code == 201
    with Session(create_engine(db_url)) as s:
        run = s.get(BacktestRun, r.json()["id"])
        assert run.status == "queued" and run.params["_costs"]["commission_per_share"] == "0.005"

@pytest.mark.db
@needs_db
def test_submit_invalid_does_not_insert(client_db: TestClient, db_url: str) -> None:
    body = valid_submit_body() | {"costs": {"commission_per_share": None, "min_commission": None, "slippage": "0.0005"}}
    r = client_db.post("/api/backtests", json=body)
    assert r.status_code == 422 and any("佣金" in e for e in r.json()["detail"])
    with Session(create_engine(db_url)) as s:
        assert s.scalar(select(func.count()).select_from(BacktestRun).where(BacktestRun.status == "queued")) == 1  # 只有 seed 的那条

def test_equity_rejects_more_than_five(client_no_db: TestClient) -> None:
    assert client_no_db.get("/api/backtests/equity", params={"ids": [1, 2, 3, 4, 5, 6]}).status_code == 422
```

把 `tests/test_dashboard_backtest.py` 中 `build_submission` 的参数化用例（`test_build_submission_ok`、`test_build_submission_errors`）改写为对 `quant.api.backtests.build_submission` 的测试，放进本文件。

- [ ] **Step 2: 写失败测试 `tests/test_api_readonly.py`**

```python
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

def test_only_backtest_submission_writes() -> None:
    app = create_app()
    writes = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods & WRITE_METHODS}
    assert writes == {("POST", "/api/backtests")}

@pytest.mark.parametrize("path", sorted(API_DIR.rglob("*.py")), ids=lambda p: p.name)
def test_api_is_read_only(path: Path) -> None:
    ...  # 沿用 tests/test_dashboard.py::test_dashboard_is_read_only 的 AST 检查（FORBIDDEN_IMPORTS / FORBIDDEN_NAMES），目录改为 src/quant/api
```

- [ ] **Step 3: 运行，确认失败**

Run: `TRADING_MODE=sim uv run pytest tests/test_api_routes.py tests/test_api_readonly.py -v`
Expected: FAIL（`ImportError: create_app`）

- [ ] **Step 4: 实现 `deps.py`、`backtests.py`、各路由与最小 `create_app()`**（只挂 `/api` 路由，`include_router(prefix="/api")`）。

- [ ] **Step 5: 运行，确认通过**

Run: `TRADING_MODE=sim QUANT_TEST_DATABASE_URL=<本地测试库> uv run pytest tests/test_api_routes.py tests/test_api_readonly.py -v && uv run mypy && uv run ruff check`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/quant/api tests/test_api_routes.py tests/test_api_readonly.py
git commit -m "feat: 面板只读接口与回测提交接口（Refs #58）"
```

---

### Task 3: 托管前端、OpenAPI 导出、容器与 CI

**Files:**
- Modify: `src/quant/api/app.py`
- Create: `src/quant/api/openapi.py`、`tests/test_api_app.py`
- Modify: `Dockerfile`、`compose.yml`、`compose.dev.yml`、`Makefile`、`.github/workflows/ci.yml`、`.gitignore`（加 `web/node_modules/`、`web/dist/`）

**Interfaces:**
- Consumes: Task 2 的 `create_app`。
- Produces：`create_app(static_dir)`：`static_dir` 存在时挂 `/assets` 静态文件；其它非 `/api` 的 GET 路径返回 `static_dir / "index.html"`；`static_dir` 默认取环境变量 `QUANT_WEB_DIR`，未设置时不托管。`python -m quant.api.openapi` 把 `create_app().openapi()` 以 `indent=2, ensure_ascii=False, sort_keys=True` 输出到 stdout。
- 启动命令：`uvicorn --factory quant.api.app:create_app --host 0.0.0.0 --port 8501`（端口保持 8501，Caddy 不改）。

- [ ] **Step 1: 写失败测试 `tests/test_api_app.py`**

```python
def test_spa_fallback_and_api_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets").mkdir(); (tmp_path / "assets" / "a.js").write_text("x")
    c = TestClient(create_app(static_dir=tmp_path))
    for p in ("/", "/backtest/128", "/stock/NVDA", "/portfolio?account=live"):
        r = c.get(p); assert r.status_code == 200 and "root" in r.text
    assert c.get("/assets/a.js").text == "x"
    r = c.get("/api/不存在"); assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")

def test_openapi_export_is_stable() -> None:
    out = subprocess.run([sys.executable, "-m", "quant.api.openapi"], capture_output=True, text=True, check=True).stdout
    assert json.loads(out)["paths"]["/api/backtests"]["post"]
    assert (REPO / "web/src/api/openapi.json").read_text() == out   # 前端生成文件与后端一致（Task 4 生成后才会通过）
```

`test_openapi_export_is_stable` 在 Task 4 提交生成文件后才通过；本任务先用 `@pytest.mark.skipif(not (REPO / "web/src/api/openapi.json").exists(), reason="Task 4 生成")`。

- [ ] **Step 2: 运行，确认 `test_spa_fallback_and_api_404` 失败**

Run: `TRADING_MODE=sim uv run pytest tests/test_api_app.py -v`
Expected: FAIL（`/backtest/128` 返回 404）

- [ ] **Step 3: 实现静态托管与 `openapi.py`。**

- [ ] **Step 4: 容器与工具链。**
  - `Dockerfile`：新增首阶段 `FROM node:24-slim AS web`，`WORKDIR /web`，复制 `web/package.json web/package-lock.json` 后 `npm ci`，再复制 `web/` 并 `npm run build`；最终阶段 `COPY --from=web /web/dist /app/web/dist`，`ENV QUANT_WEB_DIR=/app/web/dist`，`CMD` 改为上面的 uvicorn 命令。
  - `compose.yml`：dashboard 健康检查改为请求 `http://localhost:8501/api/health`。
  - `compose.dev.yml`：dashboard `command` 改为 uvicorn 命令加 `--reload --reload-dir /app/src`；保留 `TRADING_MODE: sim` 与源码挂载。
  - `Makefile`：新增 `web-install`（`cd web && npm ci`）、`web-dev`（`cd web && npm run dev`，Vite 把 `/api` 代理到 `http://127.0.0.1:8501`）、`web-check`（`cd web && npm run lint && npm run typecheck && npm run test && npm run build`）、`api-types`（`uv run python -m quant.api.openapi > web/src/api/openapi.json && cd web && npm run gen:types`）；`check` 改为 `lint test web-check`；`up` 的说明改为“http://localhost:8501”。
  - `ci.yml`：新增 job `web`（`actions/setup-node@v4`，`node-version: 24`，`cache: npm`，`cache-dependency-path: web/package-lock.json`；步骤 `npm ci`、`npm run lint`、`npm run typecheck`、`npm run test`、`npm run build`，工作目录 `web`）；`docker` job 的冒烟测试增加：后台启动镜像 `-e TRADING_MODE=sim -p 8501:8501`，`curl --retry 10 --retry-connrefused http://localhost:8501/api/health` 返回 `{"status":"ok"}`，并 `curl -s http://localhost:8501/backtest/1 | grep -q 'id="root"'`。

- [ ] **Step 5: 运行，确认通过**

Run: `TRADING_MODE=sim uv run pytest tests/test_api_app.py -v`
Expected: PASS（openapi 用例 skipped）

- [ ] **Step 6: Commit**

```bash
git add src/quant/api tests/test_api_app.py Dockerfile compose.yml compose.dev.yml Makefile .github/workflows/ci.yml .gitignore
git commit -m "chore: 面板改由 uvicorn 提供接口与前端，CI 增加前端检查（Refs #58）"
```

---

### Task 4: 前端骨架：外框、导航、账户切换、布局组件

**Files:**
- Create: `web/` 下 `package.json`、`package-lock.json`、`tsconfig.json`、`vite.config.ts`、`eslint.config.js`、`index.html`、`src/main.tsx`、`src/App.tsx`、`src/api/openapi.json`、`src/api/types.ts`、`src/api/client.ts`、`src/styles/tokens.css`、`src/styles/layout.css`、`src/hooks/useAccount.ts`、`src/components/{TopBar,AccountSwitch,Panel,Grid,DataTable,EmptyState,Stat}.tsx`、对应 `*.test.tsx`

**Interfaces:**
- Consumes: Task 3 的 `python -m quant.api.openapi`、`make api-types`。
- Produces：
  - `package.json` scripts：`dev`、`build`（`tsc -b && vite build`）、`lint`（`eslint .`）、`typecheck`（`tsc --noEmit`）、`test`（`vitest run`）、`gen:types`（`openapi-typescript src/api/openapi.json -o src/api/types.ts`）。
  - `client.ts`：`class ApiError extends Error { status: number; detail: unknown }`；`api.get<T>(path: string, params?: Record<string, string | number | string[]>): Promise<T>`；`api.post<T>(path: string, body: unknown): Promise<T>`；类型从 `types.ts` 的 `components["schemas"]` 取，导出 `type Meta = components["schemas"]["Meta"]` 等同名别名。
  - `useAccount(meta: Meta): { account: Mode; setAccount(m: Mode): void }`：读 `?account=`；值非法或该账户 `has_data=false` 时回落到 `meta.default_account`；`setAccount` 用 `replace` 更新网址参数并保留其它参数。
  - `<TopBar meta={Meta} account={Mode} onAccountChange={(m: Mode) => void} />`：Logo、导航（市场 `/`、组合 `/portfolio`、回测 `/backtest`、系统 `/system`，当前项下划线 `#2962ff`）、搜索框（输入代码回车跳 `/stock/<代码大写>`）、数据新鲜度（`meta.data_as_of` 显示为“YYYY-MM-DD 周X · 数据截至 HH:MM ET（北京 HH:MM）”，为空显示“暂无数据”）、`<AccountSwitch>`；`account === "live"` 时渲染顶部 3px `#f97316` 细条，并把 `document.title` 前加“[实盘] ”。
  - `<AccountSwitch accounts={AccountInfo[]} value={Mode} onChange={...} />`：分段按钮，只显示 `paper`、`live`，当 `trading_mode === "sim"` 时额外显示 `sim`；`has_data=false` 的按钮 `disabled` 且 `title="暂无数据"`。
  - `<Grid>` 与 `<Cell span={1..12}>`：12 列、`gap: 12px`、子项 `stretch`；`<Panel title right? scroll?>`：标题栏固定高度 22px，`scroll` 时内容区 `overflow:auto`；`<DataTable columns rows maxHeight?>`：`th/td` 不换行、表头 `position: sticky`，数值列 `align: "right"`、其余左对齐（列定义里 `align` 同时作用于表头和单元格）；`<EmptyState kind="none" | "pending" | "nodb">` 文案分别为“暂无数据”“数据接入中”“未配置数据库（QUANT_DATABASE_URL）”；`<Stat label value sub? tone?>`。
  - `tokens.css`：CSS 变量，取值见 Global Constraints；`layout.css`：`.g12`、`.panel`、表格、`num`（`font-variant-numeric: tabular-nums`）。

- [ ] **Step 1: 初始化工程**：`npm create vite@latest web -- --template react-ts`，安装 `react-router`、`lightweight-charts`，dev 依赖 `openapi-typescript`、`vitest`、`@testing-library/react`、`@testing-library/user-event`、`jsdom`、`eslint` 及 `typescript-eslint`、`eslint-plugin-react-hooks`；`vite.config.ts` 设 `server.proxy["/api"] = "http://127.0.0.1:8501"`、`test.environment = "jsdom"`。运行 `make api-types` 生成并提交 `openapi.json` 与 `types.ts`。

- [ ] **Step 2: 写失败测试**

```tsx
// useAccount.test.tsx
const meta = mkMeta({ accounts: [["sim", false], ["paper", true], ["live", false]], default_account: "paper" });
it("falls back on unknown account", () => { renderAt("/portfolio?account=foo", meta); expect(current()).toBe("paper"); });
it("falls back when account has no data", () => { renderAt("/portfolio?account=live", meta); expect(current()).toBe("paper"); });
it("keeps other params when switching", async () => { renderAt("/backtest?run=3", mkMeta({ live: true })); await switchTo("live"); expect(location()).toBe("/backtest?run=3&account=live"); });

// AccountSwitch.test.tsx
it("disables accounts without data", () => { render(<AccountSwitch accounts={[paper(true), live(false)]} value="paper" onChange={vi.fn()} />);
  expect(screen.getByRole("button", { name: "实盘" })).toBeDisabled(); });

// TopBar.test.tsx
it("shows live stripe and title prefix", () => { render(<TopBar meta={metaLive} account="live" onAccountChange={vi.fn()} />);
  expect(screen.getByTestId("live-stripe")).toBeInTheDocument(); expect(document.title.startsWith("[实盘] ")).toBe(true); });
it("formats data freshness in ET and Beijing", () => { render(<TopBar meta={mkMeta({ data_as_of: "2026-10-31T00:15:00Z" })} account="paper" onAccountChange={vi.fn()} />);
  expect(screen.getByText("2026-10-30 周五 · 数据截至 20:15 ET（北京 08:15）")).toBeInTheDocument(); });
it("search jumps to stock page", async () => { /* 输入 nvda 回车 → location 为 /stock/NVDA */ });

// DataTable.test.tsx
it("aligns header and cells the same side", () => { /* columns: [{key:"n", align:"right"}] → th 与 td 都有 class "r" */ });
```

`mkMeta`、`renderAt`、`current`、`switchTo`、`location` 写在 `src/test/utils.tsx`。

- [ ] **Step 3: 运行，确认失败**

Run: `cd web && npm run test`
Expected: FAIL（模块不存在）

- [ ] **Step 4: 实现上述组件、`App.tsx` 路由（`/`、`/portfolio`、`/backtest`、`/backtest/:id`、`/system`、`/stock/:symbol`，页面组件暂为占位，Task 5 填充）与 `main.tsx`。** `App` 启动时请求 `/api/meta`；请求失败显示全屏错误面板（文案含接口返回的 `detail`）。

- [ ] **Step 5: 运行，确认通过**

Run: `make web-check && TRADING_MODE=sim uv run pytest tests/test_api_app.py -v`
Expected: 前端 lint、类型检查、测试、构建全部通过；`test_openapi_export_is_stable` PASS（不再 skip）

- [ ] **Step 6: Commit**

```bash
git add web Makefile
git commit -m "feat: 前端骨架——顶栏、导航、账户切换与布局组件（Refs #58）"
```

---

### Task 5: 迁移现有页面功能

**Files:**
- Create: `web/src/charts/LineChart.tsx`、`web/src/charts/CandleChart.tsx`、`web/src/pages/{MarketPage,PortfolioPage,BacktestPage,BacktestSubmitDrawer,SystemPage,StockPage}.tsx`、对应 `*.test.tsx`

**Interfaces:**
- Consumes: Task 2 的接口；Task 4 的 `api`、`useAccount`、`Grid/Cell/Panel/DataTable/EmptyState/Stat`。
- Produces：
  - `<LineChart series={{ name: string; color: string; dash?: boolean; points: { time: string; value: number }[] }[]} height={number} percent?={boolean} />`（lightweight-charts 折线）
  - `<CandleChart bars={Bar[]} height={number} mas={number[]} />`（K 线 + 成交量 + 均线）
  - 各页面布局（12 列切分）与内容：

| 页面 | 布局（12 列） | 内容（全部来自已有接口） |
|---|---|---|
| 市场 `/` | 第一排 8+4：左“市场看板”`EmptyState pending`（说明：热力图、宽度、异动、板块将在后续接入）；右“我的策略”卡（当前账户的最新净值、最近 30 个快照的净值小图、最新信号日期与目标标的数、最近一次任务状态） | `/api/portfolio/{acc}/curve`、`/api/signals/latest`、`/api/system/jobs` |
| 组合 `/portfolio` | 第一排 3×4 指标卡（净值、现金、快照日、持仓数）；第二排 12 净值曲线；第三排 8+4：持仓与目标（标的、目标权重、实际权重、差异，差异 = 实际 − 目标）+ 最新信号说明；第四排 6+6：订单、成交（表格在面板内滚动，`maxHeight` 相同） | `/curve`、`/positions`、`/api/signals/latest`、`/orders`、`/fills`（均带当前账户） |
| 回测 `/backtest` | 左 3 列：回测列表（每条一行：状态点、#id、策略、年化、回撤；勾选最多 5 个）；右 9 列：所选回测的标题与指标卡（总收益、年化、年化波动、最大回撤、Sharpe、Calmar、年化换手、交易次数，格式同现 `METRIC_LABELS`）、净值对比与回撤图、指标对比表；“+ 新建”打开 `BacktestSubmitDrawer` | `/api/backtests`、`/api/backtests/equity`、`/api/backtests/defaults`、POST `/api/backtests` |
| 新建回测抽屉 | 右侧 440px 抽屉 | 字段同现表单（策略、开始、结束、标的池、避险资产、动量回看、top_k、均线窗口、初始资金、每股佣金、单笔最低佣金、滑点），默认值来自 `/defaults`；提交成功显示“已提交回测 #id”并刷新列表；422 时逐条显示中文错误 |
| 系统 `/system` | 第一排 3×4 状态卡（交易模式、熔断开关〔文案“开启（允许下单）/关闭（禁止下单）/未知”，下方注明“只能在服务器命令行修改：quant trading”〕、队列运行中、队列排队）；第二排 8+4：定时任务表 + 最近失败；第三排 12：数据状态表（标的、最新日期、行数、来源、最近写入、校验、问题） | `/api/meta`、`/api/system/*`、`/api/market/data-status` |
| 个股 `/stock/:symbol` | 第一排 12：代码标题、最新收盘与涨跌；第二排 12：`CandleChart`（MA10/20/120），区间 1/2/3/5/10/20 年切换，“含分红复权”开关（同现行情页逻辑：`factor = adj_close / close`） | `/api/market/bars/{symbol}` |

- [ ] **Step 1: 写失败测试**（Vitest，`fetch` 用 `vi.spyOn(globalThis, "fetch")` 按路径返回固定 JSON）

```tsx
it("shows database warning on 503", async () => { mockApi({ "/api/portfolio/paper/curve": [503, { detail: "未配置数据库（QUANT_DATABASE_URL）" }] });
  renderPage(<PortfolioPage />, "/portfolio"); expect(await screen.findByText("未配置数据库（QUANT_DATABASE_URL）")).toBeInTheDocument(); });
it("shows empty state for account without data", async () => { mockApi({ "/api/portfolio/paper/curve": [200, []], /* 其它同为空 */ });
  renderPage(<PortfolioPage />, "/portfolio"); expect(await screen.findAllByText("暂无数据")).not.toHaveLength(0); });
it("computes weight diff as actual minus target", async () => { /* positions: NVDA 市值 60、META 40；signal: NVDA 0.5、META 0.5 → NVDA 差异 +10.0%、META −10.0% */ });
it("submits backtest and shows errors from 422", async () => { /* POST 返回 422 detail ["请填写佣金（老虎实际费率待确认，见 issue #5）"] → 抽屉内显示该文本 */ });
it("limits comparison to five runs", async () => { /* 勾第 6 个时复选框 disabled */ });
it("market page shows pending board and my strategy card", async () => { /* 显示“数据接入中”，以及净值数字 */ });
it("system page shows kill switch read-only note", async () => { /* 文案“只能在服务器命令行修改：quant trading”，且页面内无任何 button 文案含“开启”“关闭” */ });
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd web && npm run test`
Expected: FAIL

- [ ] **Step 3: 实现图表组件与各页面。**

- [ ] **Step 4: 运行，确认通过**

Run: `make web-check`
Expected: PASS

- [ ] **Step 5: 本地联调与对齐检查。** `make up`（构建新镜像），浏览器打开 http://localhost:8501，确认五个页面可用、账户切换生效、回测可提交；用 Chrome 无头截图在 1280px 与 1600px 两个宽度下各截一张每页的图，逐页检查：同排面板底边对齐、上下列线对齐、面板间距 12px、表格不换行。

Run: `for w in 1280 1600; do for p in "" portfolio backtest system stock/SPY; do "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --window-size=$w,1600 --screenshot=/tmp/qa-$w-${p//\//_}.png "http://localhost:8501/$p"; done; done`
Expected: 10 张截图均满足上述四条；不满足的修正后重截。

- [ ] **Step 6: Commit**

```bash
git add web
git commit -m "feat: 迁移组合、回测、系统、个股页面到新前端（Refs #58）"
```

---

### Task 6: 移除 Streamlit 并更新文档

**Files:**
- Delete: `src/quant/dashboard/`、`tests/test_dashboard.py`、`tests/test_dashboard_backtest.py`、`tests/test_dashboard_pages.py`
- Modify: `pyproject.toml`（删除 `streamlit`、`plotly` 依赖及 mypy override 中的 `plotly.*`），`uv.lock`，`docs/PROJECT_PLAN.md`（第 2 章“面板”行、第 3 章架构图中 dashboard 一行、第 4 章技术选型“面板”行、第 5 章仓库结构、第 10 章标题与内容改为 FastAPI + React 并引用新设计文档），`CLAUDE.md`（`make up` 说明、新增 `make web-dev` / `make web-check` / `make api-types` 命令说明），`deploy/README.md` 中提及 Streamlit 或 `_stcore` 的地方

- [ ] **Step 1: 确认没有其它代码引用旧面板**

Run: `grep -rn "quant.dashboard\|streamlit\|plotly" src tests scripts configs Dockerfile compose*.yml Makefile .github deploy`
Expected: 只剩待删除的文件与本任务要改的文档行

- [ ] **Step 2: 删除旧面板与依赖**：`git rm -r src/quant/dashboard tests/test_dashboard*.py`，`uv remove streamlit plotly`，删 mypy override 中的 `"plotly.*"`。

- [ ] **Step 3: 更新文档**（按上面 Files 列表逐处修改；`PROJECT_PLAN.md` 第 10 章写明：面板只读，唯一写操作是提交回测，由 `tests/test_api_readonly.py` 保证）。

- [ ] **Step 4: 全量检查**

Run: `make check && docker build -t quant:local . && docker run --rm -d --name qa -e TRADING_MODE=sim -p 8501:8501 quant:local && sleep 5 && curl -s http://localhost:8501/api/health && curl -s http://localhost:8501/system | grep -c 'id="root"'; docker rm -f qa`
Expected: `make check` 全部通过；health 返回 `{"status":"ok"}`；最后一行输出 `1`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: 移除 Streamlit 面板，更新项目文档（Refs #58）"
```

---

## 交付后（不属于本计划的代码任务）

- 按 CLAUDE.md：功能分支合入 `test` 并 push，CI 通过后在测试环境验证；再由 `test` 发 PR 到 `main`，部署后用现有域名和登录密码访问，确认 Caddy 无需修改。
- 后续步骤（组合页、个股页增强、服务器回测数据、回测页七个标签、数据源接入、首页看板、系统页）各自另写计划。
