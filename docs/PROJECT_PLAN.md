# 美股量化交易系统：项目方案与交接文档

> 本文档用于交接给 Claude Code，作为项目的唯一设计依据。
> 建议放在仓库 `docs/PROJECT_PLAN.md`，并在根目录 `CLAUDE.md` 中引用。
> 文档中标注【待确认】的内容尚未验证，实施前需要先确认，不要凭假设实现。

---

## 0. 当前状态与第一步

- **当前阶段：Phase 0 可行性验证**（见第 15 章）。服务器尚未购买。
- 所有 Phase 0 工作在 Mac 本地完成，零成本。
- **Claude Code 的第一个任务**：Phase 0 任务 0.1，编写老虎 OpenAPI 连通性验证脚本。

---

## 1. 项目背景与目标

个人美股量化交易系统。目标是搭建一套从数据采集、研究回测到自动交易、可视化监控的完整链路，先以简单策略跑通全流程，再逐步扩展策略。

**非目标（当前阶段不做）**：高频交易、做市、日内交易、杠杆、多账户、多用户。

---

## 2. 已确定的约束与决策

| 类别 | 决策 |
|---|---|
| 券商 | 老虎证券（Tiger Brokers），通过官方 OpenAPI（Python SDK `tigeropen`）交易 |
| 资金 | 香港银行卡出入金，美元；规模 2.5 万美元以下 |
| 策略频率 | 日线级别（收盘后算信号，次日开盘后执行），不做日内交易 |
| 开发环境 | Mac（可能是 Apple Silicon），只在本地写代码 |
| 代码仓库 | GitHub 私有仓库，是 Mac 与服务器之间唯一的通道 |
| 运行环境 | 海外 Linux 云服务器，只运行 CI 部署的内容，**不在服务器上写代码、不手动改文件** |
| 部署方式 | Docker Compose；镜像由 GitHub Actions 构建并推送到 GHCR |
| 架构原则 | 研究回测与交易执行分离；Broker 抽象为接口；策略只输出目标仓位 |
| 面板 | 网页面板查看行情、回测、交易、系统状态；**只读，不能下单**；可以提交回测任务 |
| 网络访问 | 面板不暴露公网，通过 Tailscale 私有网络访问 |
| 存储 | 行情数据用 Parquet（DuckDB 查询）；交易状态与回测结果用 PostgreSQL |

---

## 3. 系统架构

```
Mac（开发）
  ├─ 写代码、单元测试
  ├─ docker compose 本地起全套服务（与服务器一致）
  └─ git push
        ↓
GitHub
  ├─ PR：ruff + mypy + pytest
  └─ main：构建 linux/amd64 镜像 → 推送 GHCR → SSH 到服务器部署
        ↓
云服务器（Docker Compose）
  ├─ postgres         交易状态、信号、回测结果
  ├─ scheduler        定时任务：数据采集 → 信号 → 风控 → 执行 → 对账
  ├─ backtest-worker  从 Postgres 队列领取回测任务执行（限制 CPU/内存）
  ├─ dashboard        Streamlit 面板（只读 + 提交回测）
  └─ 卷 /data/parquet 行情数据
        │
        ├─→ 老虎 OpenAPI（先模拟账户，后实盘）
        ├─→ 数据源（yfinance / 老虎 K 线）
        ├─→ 飞书或企业微信 webhook 告警
        └─→ 对象存储（每日备份）
```

**设计要点**

- 所有服务使用**同一个 Docker 镜像**，通过不同启动命令区分角色，降低构建和维护成本。
- 回测任务队列直接用 Postgres 表实现（`SELECT ... FOR UPDATE SKIP LOCKED`），不引入 Redis 或消息队列。
- 回测 worker 设置 CPU 和内存上限，保证回测不影响交易任务。

---

## 4. 技术选型

| 方面 | 选择 |
|---|---|
| 语言 | Python 3.12 |
| 依赖管理 | uv（提交 `uv.lock`） |
| 代码质量 | ruff（lint + format）、mypy、pytest |
| 配置 | pydantic-settings，YAML 配置 + 环境变量覆盖 |
| 数据处理 | pandas、numpy、pyarrow、duckdb |
| 数据库 | PostgreSQL 16，SQLAlchemy 2.x + psycopg 3，Alembic 管理迁移 |
| 交易日历 | exchange_calendars（识别休市日、半日市） |
| 时区 | 标准库 zoneinfo，业务时间统一用 `America/New_York` |
| 调度 | 自写的轻量调度循环（`jobs/scheduler.py`，scheduler 容器内）：按 XNYS 日历与美东时间触发；未用 APScheduler，因其 cron 触发器无法表达交易日与半日市（#32） |
| 数据源 | yfinance（回测历史数据）、tigeropen（行情与交易） |
| 快速研究 | vectorbt（可选，用于参数探索） |
| 面板 | Streamlit + Plotly |
| 部署 | Docker、Docker Compose、GitHub Actions、GHCR |

---

## 5. 仓库结构

```
quant/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── compose.yml              # 服务定义（服务器与本地共用）
├── compose.dev.yml          # 本地覆盖（端口映射、挂载源码等）
├── Makefile                 # 常用命令：test / lint / up / sync-data
├── .env.example             # 环境变量模板（不含真实值）
├── configs/
│   ├── base.yaml
│   ├── dev.yaml             # 本地：回测 Broker
│   ├── paper.yaml           # 老虎模拟账户
│   └── live.yaml            # 老虎实盘
├── src/quant/
│   ├── core/                # 领域模型与通用工具
│   │   ├── models.py        # Bar, Quote, Position, AccountSnapshot, OrderRequest, OrderStatus, Fill
│   │   ├── calendar.py      # 交易日历封装
│   │   └── settings.py      # 配置加载
│   ├── broker/
│   │   ├── base.py          # Broker 抽象接口
│   │   ├── tiger.py         # 老虎实现（模拟/实盘由配置切换）
│   │   └── sim.py           # 回测用模拟撮合（佣金、滑点）
│   ├── data/
│   │   ├── providers/
│   │   │   ├── base.py      # DataProvider 接口
│   │   │   ├── yfinance.py
│   │   │   └── tiger.py
│   │   ├── store.py         # Parquet 读写 + DuckDB 查询
│   │   └── validate.py      # 数据校验
│   ├── strategies/
│   │   ├── base.py          # Strategy 抽象接口、Context
│   │   └── etf_momentum.py  # 第一个策略
│   ├── risk/
│   │   └── rules.py         # 风控规则
│   ├── engine/
│   │   ├── executor.py      # 目标仓位 → 订单
│   │   ├── backtester.py    # 回测引擎
│   │   └── reconcile.py     # 对账
│   ├── jobs/
│   │   ├── scheduler.py     # 调度入口（按交易日历触发每日任务）
│   │   ├── daily_data.py
│   │   ├── daily_signal.py
│   │   ├── daily_execute.py
│   │   └── backtest_worker.py
│   ├── db/
│   │   ├── models.py        # SQLAlchemy 表定义
│   │   └── migrations/      # Alembic
│   ├── notify/
│   │   └── webhook.py       # 飞书/企业微信告警
│   └── dashboard/
│       ├── app.py
│       └── pages/           # 行情、回测、交易、系统
├── scripts/
│   └── phase0/              # 可行性验证脚本
├── research/                # notebooks，不参与部署
├── tests/
├── deploy/
│   └── server_init.md       # 服务器初始化步骤
└── .github/workflows/
    ├── ci.yml
    └── deploy.yml
```

---

## 6. 核心接口设计

### 6.1 Broker 接口

```python
class Broker(ABC):
    @abstractmethod
    def get_account(self) -> AccountSnapshot: ...      # 现金、净值、购买力
    @abstractmethod
    def get_positions(self) -> dict[str, Position]: ...
    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    @abstractmethod
    def place_order(self, req: OrderRequest) -> str: ...  # 返回券商订单号
    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...
    @abstractmethod
    def get_order(self, order_id: str) -> OrderStatus: ...
    @abstractmethod
    def get_open_orders(self) -> list[OrderStatus]: ...
```

实现：

- `TigerBroker`：封装 `tigeropen`。模拟账户和实盘账户只是配置不同（账户号、环境），代码相同。
- `SimBroker`：回测用。按下一根 K 线的开盘价成交，叠加可配置的佣金和滑点。

要求：

- 领域模型不暴露任何 `tigeropen` 的类型，SDK 只出现在 `broker/tiger.py` 和 `data/providers/tiger.py` 中。
- `OrderRequest` 必须包含确定性的 `client_order_id`（见第 8 章幂等设计）。
- 金额相关字段用 `Decimal`，权重用 `float`。

### 6.2 Strategy 接口（目标仓位模式）

```python
@dataclass(frozen=True)
class Context:
    as_of: date                     # 信号日（收盘后）
    bars: Callable[..., pd.DataFrame]  # 只能取到 as_of 及之前的数据
    params: Mapping[str, Any]

class Strategy(ABC):
    name: str
    @abstractmethod
    def target_weights(self, ctx: Context) -> dict[str, float]:
        """返回目标仓位权重，如 {"QQQ": 0.6, "TLT": 0.4}；权重和 ≤ 1，剩余为现金。"""
```

要求：

- 策略是纯计算，不做任何 I/O，不下单，不读写数据库。
- `Context.bars` 在实现上强制截断到 `as_of`，从机制上防止未来函数。
- **回测和实盘使用同一个策略类**，只是注入的数据与 Broker 不同。

### 6.3 Executor（目标仓位 → 订单）

1. 读取账户净值、当前持仓和最新报价。
2. 计算目标持股数：`floor(净值 × 目标权重 × (1 - 现金缓冲) / 价格)`。现金缓冲默认 2%。
3. 与当前持仓做差，得到买卖数量。
4. **先卖后买**，卖单成交后再根据实际现金计算买单。
5. 低于最小交易阈值的调整跳过（默认：金额小于 100 美元，或偏离目标权重小于 2 个百分点），避免无意义的小额交易。
6. 所有订单先经过风控（6.4），再提交。
7. 使用限价单：买单价为最新卖价上浮少量，卖单价为最新买价下浮少量（可配置，默认 0.2%）。超时未成交则撤单并按最新报价重新挂单，最多重试 N 次，仍失败则告警并停止（#30 确认：超时 5 分钟、最多重试 3 次；被券商拒绝时不重试）。
8. 报价来源（Phase 0 决定，#3）：模拟盘阶段不购买实时行情，参考价取老虎免费延迟行情（约 15 分钟延迟，无买卖价时用最新价），因此执行时间不早于开盘后 20 分钟（默认 09:50 ET，见第 8 章）。每笔订单记录参考价与实际成交价；Phase 5 切实盘前按偏差决定是否购买 Nasdaq Basic（USD 35/月），偏差不大则改为放宽限价缓冲或推迟执行时间。

### 6.4 风控规则

在 Executor 生成订单之后、提交之前统一校验，任一规则不通过即拒绝整批订单并告警：

- **标的白名单**：只能交易配置中列出的代码。
- **单标的权重上限**：默认 100%（单策略轮动），可配置。
- **总仓位上限**：权重和不超过 100%，不使用保证金。
- **单笔订单金额上限**：按账户配置（`risk.max_order_usd`）；模拟盘 150,000 美元，只防异常；实盘切换前另行确认，未配置时拒绝执行（#30）。
- **单日换手上限**：当日成交额 / 净值不超过 2.2（`risk.max_daily_turnover`；月度全仓轮动约 2，#30）。
- **数据新鲜度**：信号所用数据日期必须是最近一个交易日，否则停止。
- **对账一致性**：本地记录的持仓与券商持仓不一致时，停止交易并告警，等待人工处理。
- **熔断开关**：数据库中的 `trading_enabled` 标志为 false 时，不提交任何订单。
- **交易模式保护**：`TRADING_MODE` 只能取 `sim` / `paper` / `live`，默认 `paper`；`live` 必须在服务器 `.env` 中显式设置。

---

## 7. 数据设计

### 7.1 数据源

| 用途 | 来源 | 说明 |
|---|---|---|
| 回测历史日线 | yfinance | 免费，取复权数据；不稳定，需要重试和校验 |
| 实盘行情、报价 | 老虎 OpenAPI | 美股实时行情需单独购买（Nasdaq Basic，USD 35/月）；模拟盘阶段只用免费延迟行情（约 15 分钟），见 Phase 0 报告 0.2 |
| 交易日历 | exchange_calendars | 交易所 `XNYS` |

`DataProvider` 抽象为接口，以后可以接入付费数据源（如 Polygon、Tiingo）而不影响上层代码。

### 7.2 Parquet 存储布局

```
/data/parquet/
└── bars/daily/
    └── symbol=QQQ/data.parquet
```

字段：`date, open, high, low, close, adj_close, volume, source, ingested_at`。
写入采用"读取 → 合并去重 → 原子替换文件"，保证重跑幂等。

### 7.3 数据校验（`data/validate.py`）

- 交易日缺失（对照交易日历）
- 价格非正数或为空
- 单日涨跌幅异常（默认 ETF 超过 15% 标记，需人工确认）
- 两个数据源抽样比对，差异超过阈值时告警

校验失败时，后续信号任务不得运行。

### 7.4 PostgreSQL 表

| 表 | 主要字段 | 说明 |
|---|---|---|
| `job_runs` | job_name, trade_date, status, started_at, finished_at, error | `(job_name, trade_date)` 唯一，保证幂等 |
| `signals` | strategy, signal_date, target_weights (jsonb), data_as_of | 每日目标仓位 |
| `orders` | client_order_id, broker_order_id, symbol, side, qty, limit_price, status, mode | `client_order_id` 唯一 |
| `fills` | broker_order_id, symbol, qty, price, commission, filled_at | 成交记录 |
| `position_snapshots` | snapshot_date, symbol, qty, avg_cost, market_value | 每日持仓快照 |
| `account_snapshots` | snapshot_date, cash, net_liquidation | 每日账户快照 |
| `backtest_runs` | id, strategy, params (jsonb), start, end, status, metrics (jsonb), error, created_at | 兼作回测任务队列 |
| `backtest_equity` | run_id, date, equity, drawdown | 净值曲线 |
| `system_flags` | key, value | 如 `trading_enabled` |

实现补充（#14，`src/quant/db/models.py`，初始迁移见 `src/quant/db/migrations/versions/`）：

- `orders`、`fills`、两张快照表增加 `mode` 列（sim / paper / live），模拟盘与实盘数据在同一个库中不混淆；快照表按 `(日期, mode[, symbol])` 唯一。
- `orders` 增加 `reference_price`（下单时参考价）、`filled_qty`、`avg_fill_price`、`reason`，用于评估延迟行情下的执行偏差（#3）。
- `signals` 按 `(strategy, signal_date)` 唯一。状态、方向、模式等字段用 CHECK 约束限定取值。
- 初始迁移写入 `trading_enabled = false`：熔断开关默认关闭，需人工打开。

---

## 8. 每日运行流程

所有时间为美东时间，由 `jobs/scheduler.py` 按 `America/New_York` 与 XNYS 日历调度，自动处理夏令时。非交易日不运行，半日市按实际收盘时间调整。

- 常规交易时段 09:30–16:00 ET；半日市（如感恩节次日、平安夜）13:00 收盘，`daily_data` 相应提前到 13:30 后。交易日、半日市一律以 `exchange_calendars` 的 `XNYS` 为准，不硬编码。
- `daily_execute` 只在常规交易时段内下单，不做盘前盘后；超过收盘前 30 分钟仍未完成则撤单并告警。
- 执行时间默认 09:50：模拟盘阶段用约 15 分钟延迟行情（6.3 第 8 条），09:35 拿到的是开盘前的价格；09:50 时延迟价已反映开盘后成交，同时避开开盘最初几分钟的剧烈波动。买实时行情后可改回 09:35。
- 北京时间对照（仅供人工查看，代码不使用）：夏令时（3 月第二个周日至 11 月第一个周日）开盘为 21:30，冬令时为 22:30；美国与中国的时差在夏令时切换日会变化。

| 时间（ET） | 任务 | 内容 |
|---|---|---|
| 收盘后 16:30 | `daily_data` | 拉取当日日线入库 → 校验 → 失败则告警并终止当日后续任务 |
| 17:00 | `daily_signal` | 运行策略 → 写入 `signals` → 推送"明日计划调仓"通知 |
| 次日 09:50（可配置） | `daily_execute` | 熔断检查 → 对账 → 生成订单 → 风控 → 下单 → 跟踪成交 |
| 执行完成后 | `daily_report` | 持仓与账户快照 → 再次对账 → 推送日报 |

**幂等设计**

- 每个任务开始时检查 `job_runs`，同一交易日已成功的不再重复执行。
- `client_order_id = "{trade_date}-{strategy}-{symbol}-{side}-{attempt}"`，重复提交时可识别。
- 执行任务重启后，先查询券商的未完成订单和当日成交，再决定是否继续，不得盲目重新下单。

**通知时机**：任务失败、风控拒绝、对账不一致、订单超时未成交、每日日报。

**调度与人工操作**（#32）：
- `job_runs` 中已成功的任务不再执行；失败的不自动重跑，人工确认后 `uv run quant job <任务> --date <交易日> --rerun-failed`；停留在 running 的（进程中途退出）重启后自动重跑。同一任务用 PostgreSQL advisory lock 防止并发。
- 收盘后数据未出（缺当日、数据过期）时等待 15 分钟重试，最多 3 次。
- scheduler 启动时已过当日执行时段则不再下单，有待执行的信号时提醒一次。
- 首次启用模拟盘：先 `uv run quant baseline` 把券商当前持仓记为对账基准，再 `uv run quant trading enable --yes` 人工打开熔断开关。

**执行器的恢复规则**（`engine/executor.py`，#30）：订单先写入 `orders` 再提交，拿到券商订单号后补写。重启时：本策略当日的未完成订单先撤销并同步成交，再按实际持仓重新计算（目标仓位模式天然幂等）；券商上有其他未完成订单、或本地有订单没有券商订单号（提交结果未知），一律熔断等人工处理，不盲目重新下单。

---

## 9. 回测设计

- `Backtester` 按交易日循环：在每个信号日调用策略得到目标权重，次日开盘通过 `SimBroker` 成交，复用 `Executor` 的下单逻辑（包括最小交易阈值）。
- 成本模型可配置：每股佣金、单笔最低佣金、滑点（默认按成交价 0.05%）。老虎实际费率【待确认】，确认后写入配置。
- 输出指标：年化收益、年化波动、最大回撤、Sharpe、Calmar、换手率、交易次数，以及与基准（默认买入持有 SPY）的对比。
- 结果写入 `backtest_runs` 和 `backtest_equity`，面板读取展示。

**任务化执行**

1. 面板提交回测：选择策略、填写参数和区间 → 插入一行 `status = 'queued'`。
2. `backtest-worker` 轮询领取任务（`FOR UPDATE SKIP LOCKED`）→ 执行 → 写入结果，状态改为 `done` 或 `failed`。

**必须规避的偏差**

- 未来函数：由 `Context` 截断机制保证，并编写专门的单元测试验证。
- 幸存者偏差：第一阶段只用长期存在的大型 ETF，影响很小；以后做个股选股时必须换用包含退市股票的数据源。
- 过拟合：参数研究必须保留样本外区间，并做 walk-forward 验证。

---

## 10. 面板（Streamlit）

| 页面 | 内容 |
|---|---|
| 行情 | 标的 K 线，数据最新日期，校验状态 |
| 回测 | 提交回测表单；历史回测列表；净值与回撤曲线；多个回测的指标对比 |
| 交易 | 当前持仓、目标仓位与实际仓位的差异、订单与成交记录、账户净值曲线 |
| 系统 | 各任务最近运行状态、最近告警、交易模式与熔断开关状态（只读显示） |

约束：面板**不得包含任何下单、撤单、修改熔断开关的功能**。唯一的写操作是提交回测任务。

---

## 11. 配置、密钥与环境

| 环境 | 位置 | `TRADING_MODE` | Broker |
|---|---|---|---|
| 本地开发 | Mac | `sim` | SimBroker；需要时可连老虎模拟账户 |
| 模拟盘 | 服务器 | `paper` | TigerBroker（模拟账户） |
| 实盘 | 服务器 | `live` | TigerBroker（实盘账户） |

- 老虎的 `tiger_id`、私钥、账户号只通过环境变量或服务器上权限为 600 的文件提供。
- `.env`、私钥文件、`*.properties` 必须加入 `.gitignore`，仓库中只保留 `.env.example`。
- 实盘密钥只存在于服务器，Mac 上最多配置模拟账户。
- 配置由 `core/settings.py` 加载：`configs/base.yaml` → 按模式叠加 `dev/paper/live.yaml` → `QUANT_` 前缀环境变量覆盖。`TRADING_MODE` 只能来自环境变量，写在 YAML 中会拒绝启动；Mac 上设置 `live` 会拒绝启动。
- 向券商下单前统一调用 `core/safety.py` 的 `check_order_allowed`：`paper` 只允许模拟账户，`live` 只允许实盘账户且不能在开发机上运行。
- GitHub Secrets 中只存部署所需的 SSH 私钥和服务器地址。

---

## 12. CI/CD

**`ci.yml`（PR 与 push）**：`uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest`（含一个使用固定样本数据的回测冒烟测试）。

**`deploy.yml`（main 分支，Phase 4 起启用）**：

1. 构建 `linux/amd64` 镜像。Mac 若为 Apple Silicon，本地镜像是 arm64，**不要在本地构建后推送到服务器**，统一由 CI 构建。
2. 推送到 GHCR，标签为 commit SHA 和 `latest`。
3. SSH 到服务器：`docker compose pull` → 运行 Alembic 迁移 → `docker compose up -d`。
4. 部署后检查各服务健康状态，失败则告警。
5. 回滚：将 compose 中镜像标签改回上一个 SHA 并重新部署。

**数据同步**：`make sync-data` 通过 rsync（经 Tailscale）把服务器上的 Parquet 同步到 Mac，用于本地研究。

---

## 13. 服务器

### 13.1 选型（Phase 0 结束后再购买）

- 腾讯云轻量应用服务器，海外地域。候选：东京、新加坡、首尔（2 核 8G 约 95 元/月），香港（2 核 8G 约 120 元/月）。中国内地地域排除。
- 配置：2 核 8G、80G SSD。最终是否需要 8G 以 Phase 0 任务 0.5 的实测为准。
- 镜像：Ubuntu 24.04 LTS（不选 26.04）。登录方式：SSH 密钥。开启自动续费。
- 购买前可用按量计费的 CVM 测试各地域网络【是否支持以购买页为准】。

### 13.2 初始化（写入 `deploy/server_init.md`）

1. 创建普通用户并加入 docker 组；禁用 root 登录和密码登录。
2. 配置防火墙：公网只开放 SSH（以后可进一步限制为仅 Tailscale）。
3. 安装 Docker Engine 与 Compose 插件。
4. 安装 Tailscale 并加入私有网络；面板只监听 Tailscale 网卡或 localhost。
5. 系统时区保持 UTC，业务时区由应用处理。
6. 配置每日备份：`pg_dump` 与 Parquet 打包，上传到对象存储，保留 30 天。
7. 配置日志轮转（Docker 日志大小限制）。

---

## 14. 第一个策略：ETF 动量轮动（基线策略）

**定位**：用于验证整条链路，并作为以后新策略的对照基准，不追求收益最大化。

- **标的池**：SPY、QQQ、TLT、GLD；避险资产 SHY（或 BIL）。
- **信号**：每月最后一个交易日计算各标的过去 N 个交易日收益率（默认 126，约 6 个月）。
- **选股**：持有收益率最高的 1 只（参数 `top_k`，默认 1）。
- **绝对动量过滤**：若选中标的收盘价低于其 200 日均线，或其收益率低于 SHY，则全部持有 SHY。
- **调仓**：月度调仓；非调仓日策略返回与上次相同的目标权重。
- **参数**：`lookback`、`top_k`、`sma_window`、`universe`、`safe_asset`，全部可配置，便于在面板上做对比回测。

---

## 15. 分阶段路线

### Phase 0：可行性验证（当前阶段，Mac 本地，零成本）

| # | 任务 | 交付物 | 通过标准 |
|---|---|---|---|
| 0.1 | 老虎 OpenAPI 连通性 | `scripts/phase0/tiger_check.py` | 依次完成：读取配置 → 查询账户 → 查询持仓 → 获取 SPY 实时报价 → 拉取 SPY 历史日线 → 在**模拟账户**以远离市价的限价挂一笔 1 股买单 → 查询订单状态 → 撤单。每一步打印结果，任一步失败时打印清晰的错误信息并继续后续可执行的步骤 |
| 0.2 | 行情权限与历史数据 | 验证报告 `scripts/phase0/REPORT.md` | 明确：API 实时行情是否收费；历史日线最早能回溯到哪一年；接口频率限制 |
| 0.3 | 回测数据质量 | `scripts/phase0/data_check.py` | 用 yfinance 拉取 5 个标的 10 年以上日线，与老虎 K 线抽样比对复权收盘价，差异在可接受范围内 |
| 0.4 | 账户规则（人工向老虎客服确认） | 写入 `REPORT.md` | 明确：是否受 PDT 限制；是否支持碎股；美股佣金与最低收费 |
| 0.5 | 资源占用实测 | 最小原型 + `docker stats` 记录 | 本地起 postgres、scheduler、backtest-worker、dashboard，跑一次 10 年回测，记录内存峰值，据此确定服务器配置 |
| 0.6 | 服务器网络测试 | 写入 `REPORT.md` | 在候选地域用按量计费机器测试从家中和公司访问的延迟，选定地域 |

**Go/No-Go**：0.1 与 0.2 是关键项。若老虎 API 无法使用，或行情成本明显超出预期，暂停后续阶段，重新评估数据源或券商。

**0.1 实现提示**

- 安装：`uv add tigeropen`（Python SDK 要求 3.8 以上）。
- 开发者注册后获得 `tiger_id`、RSA 私钥（官方推荐 PKCS#8，兼容 PKCS#1）、`license` 和账户号；模拟账户有独立的账户号（17 位数字），`tiger_id` 与私钥实盘、模拟共用。
- SDK 中有 `TigerOpenClientConfig`、`QuoteClient`、`TradeClient` 等类。**具体方法名和参数以官方文档为准**，不要凭记忆调用：https://docs.itigerup.com/docs/prepare
- 配置使用开发者中心导出的 `tiger_openapi_config.properties`（权限 600，放在仓库外），通过环境变量 `TIGER_PROPS_PATH` 指定所在目录；脚本启动时若检测到的不是模拟账户，必须拒绝执行下单步骤。

### Phase 1：项目骨架

uv 项目、目录结构、配置加载、领域模型、Broker 与 Strategy 接口、`SimBroker`、Docker 与 compose、Alembic 初始迁移、CI。

**完成标准**：`make test` 与 `make lint` 通过；`docker compose up` 能在 Mac 上启动 postgres 和一个空的 dashboard。

### Phase 2：数据管道与回测

yfinance 与老虎数据提供者、Parquet 存储、数据校验、回测引擎、ETF 动量策略、回测结果入库。

**完成标准**：命令行可运行 ETF 动量策略 10 年回测，指标合理，未来函数测试通过。

### Phase 3：面板

四个页面；回测任务队列与 worker。

**完成标准**：在面板提交回测，worker 执行，结果在面板展示与对比。

### Phase 4：服务器部署与模拟盘

购买服务器、初始化、Tailscale、部署流水线、`TigerBroker` 完整实现、Executor、风控、对账、调度、告警、备份。

**完成标准**：模拟账户连续 20 个交易日无人工干预自动运行，日报正常；人为制造的异常（数据缺失、对账不一致）能被正确拦截并告警。之后继续运行 1 到 3 个月，比较模拟盘与回测的偏差。

### Phase 5：小资金实盘

仅切换配置为 `live`，先使用计划资金的一小部分。需要用户明确确认后才可切换。

### Phase 6：扩展

新策略（多因子、期权等）、多策略资金分配、付费数据源。

---

## 16. 待确认事项

- [x] 老虎 API 实时行情是否需要单独购买，费用多少：需单独购买，Nasdaq Basic USD 35/月；模拟盘阶段用延迟行情（#3）
- [x] 老虎 API 历史日线可回溯年限与频率限制：见 Phase 0 报告 0.2；回测以 yfinance 含分红复权价为准
- [x] 账户是否适用 PDT 规则：2026-06-04 起 FINRA 取消 PDT 与 25,000 美元门槛，净值超过 2,000 美元不限日内交易次数；本系统不做日内交易，不受影响（#5，见 Phase 0 报告 0.4）
- [x] 是否支持碎股交易：文档注明仅综合账户支持，模拟账户不支持；按整股设计
- [ ] 美股佣金与最低收费，写入回测成本模型
- [ ] 服务器地域与配置（取决于 Phase 0 任务 0.5、0.6）
- [x] 告警渠道：企业微信群机器人 webhook（#36）

---

## 17. 给 Claude Code 的工作规则

1. **安全第一**：任何情况下不得连接实盘账户下单。开发和测试只用 `sim` 或老虎模拟账户。代码中涉及下单的路径必须检查 `TRADING_MODE`。
2. **不提交密钥**：不得将任何密钥、账户号、`.env` 写入仓库或日志。日志中账户号需脱敏。
3. **以官方文档为准**：调用 `tigeropen` 前查阅官方文档，不确定的接口先写小脚本验证。
4. **按阶段推进**：完成当前阶段的完成标准后再进入下一阶段；遇到【待确认】事项先问用户。
5. **保持接口边界**：SDK 类型不越过 `broker/` 与 `data/providers/`；策略保持纯计算。
6. **测试**：核心逻辑（Executor、风控、回测、策略、数据校验）必须有单元测试；修改接口时同步更新测试。
7. **代码风格**：全量类型注解；ruff 格式化；函数保持短小；日志使用结构化格式（含 job 名称、交易日）。
8. **变更说明**：每完成一个任务，简要说明改了什么、如何验证、还有哪些未完成。

---

*免责声明：本方案为技术与工程设计，不构成投资建议。策略收益不作任何保证，实盘前需自行评估风险。*
