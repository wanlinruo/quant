# CLAUDE.md

个人美股量化交易系统。完整设计见 `docs/PROJECT_PLAN.md`，开始任何工作前先读它。

## 当前阶段

Phase 0 可行性验证。第一个任务：`scripts/phase0/tiger_check.py`（见 PROJECT_PLAN.md 第 15 章任务 0.1）。

## 关键约束

- 券商：老虎证券 OpenAPI（`tigeropen`）。接口用法以官方文档为准：https://quant.itiger.com/openapi/zh/python/overview/introduction.html
- Mac 只做开发；服务器只运行 CI 部署的 Docker 镜像，不在服务器上改代码。
- Broker 抽象为接口；策略只输出目标仓位权重，纯计算、无 I/O。
- 回测与实盘共用同一个策略类。
- 面板只读，唯一写操作是提交回测任务。

## 安全规则（必须遵守）

- 禁止连接实盘账户下单。开发测试只用 `TRADING_MODE=sim` 或老虎模拟账户。
- 下单相关代码必须检查 `TRADING_MODE`；非模拟账户时拒绝执行。
- 不得把密钥、账户号、`.env`、私钥文件提交到仓库或写入日志。
- 遇到文档中标注【待确认】的事项，先问用户，不要自行假设。

## 常用命令（Phase 1 起可用）

```
make lint      # ruff + mypy
make test      # pytest
make up        # docker compose 本地启动
make sync-data # 从服务器同步 Parquet 数据
```

## 工作方式

按阶段推进，满足当前阶段完成标准后再进入下一阶段。每完成一个任务，说明改了什么、如何验证、还有什么未完成。
