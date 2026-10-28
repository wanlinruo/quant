# quant

个人美股量化交易系统：老虎证券 OpenAPI 下单，日频 ETF 轮动起步。完整设计见 [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md)，开发约定见 [`CLAUDE.md`](CLAUDE.md)。

> 当前阶段：Phase 1 项目骨架。Phase 0 可行性验证结果见 [`scripts/phase0/REPORT.md`](scripts/phase0/REPORT.md)。

## 快速开始

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```
make install   # 安装依赖
make check     # lint + test，与 CI 相同
make help      # 全部命令
```

连接老虎模拟账户（可选）：

```
cp .env.example .env   # 按注释填写；.env 已被 gitignore
```

`TIGER_PROPS_PATH` 指向开发者中心导出的 `tiger_openapi_config.properties` 所在目录，放在仓库外，权限 600。Mac 上只允许配置模拟账户。

## 目录

| 路径 | 内容 |
|---|---|
| `src/quant/core` | 领域模型、交易日历、配置加载 |
| `src/quant/broker` | Broker 接口；老虎实现、回测模拟撮合 |
| `src/quant/data` | 数据源（yfinance、老虎）、Parquet 存储、数据校验 |
| `src/quant/strategies` | 策略，只输出目标仓位权重 |
| `src/quant/risk` | 风控规则 |
| `src/quant/engine` | 执行器、回测引擎、对账 |
| `src/quant/jobs` | 定时任务（按美东时间调度） |
| `src/quant/db` | 数据库表与迁移 |
| `src/quant/notify` | 告警 |
| `src/quant/dashboard` | 只读面板 |
| `configs/` | YAML 配置（base / dev / paper / live） |
| `scripts/` | 一次性脚本，如 Phase 0 验证 |
| `research/` | notebooks，不参与部署 |
| `deploy/` | 服务器初始化与部署文档 |

## 开发流程

issue 驱动：`feat/<issue#>-xxx` 等分支直接合并到 `test`（测试环境），`test` 经 PR 合入 `main`（正式环境）。详见 `CLAUDE.md`。

## 安全

- 开发测试只用 `TRADING_MODE=sim` 或老虎模拟账户，下单代码必须检查交易模式。
- 不提交密钥、账户号、`.env`、私钥文件；仓库目前公开。
