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

## 开发流程（issue 驱动）

所有需求、任务、缺陷都先建 issue（GitHub：`wanlinruo/quant`），再开发。分支流向：`feat/*`、`fix/*` 等 --merge--> `test`（测试环境）--PR--> `main`（正式环境）。

1. **建 issue**：使用模板（需求/任务、缺陷、待确认事项），打 `type:*`、`area:*` 标签，挂到对应 Phase 里程碑。
   - 含【待确认】事项的加 `needs-confirmation`，确认前不得实施。
   - 涉及下单、交易模式、密钥的加 `safety`。
2. **开分支**：从 `test` 切出，命名 `<类型>/<issue号>-<简短描述>`，小写加连字符：
   - `feat/` 新功能，`fix/` 缺陷修复，`chore/` 杂项/工程配置，`docs/` 文档，`refactor/` 重构
   - 例：`feat/2-tiger-check`、`fix/15-calendar-half-day`
   - 不要用 `test/` 前缀（与 `test` 分支冲突）。
3. **合入 `test`**：本地 lint/test 通过后，直接合并进 `test` 并 push（不发 PR），push 触发 CI：
   ```
   git switch test && git pull && git merge --no-ff feat/2-tiger-check && git push
   ```
   commit message 中引用 issue（如 `Refs #2`）。合并后删除功能分支。
4. **`test` → `main`**：`test` 的 CI 通过、测试环境验证无误后，由 `test` 发 PR 合入 `main`（merge commit，不 squash）。PR 描述按模板列出本次包含的 issue，写 `Closes #2` 等；合入 `main` 时 issue 自动关闭。

**分支与环境**

| 分支 | 环境 | 规则 |
|---|---|---|
| `main` | 正式环境（Phase 4 起由 CI 部署），默认分支 | 只接受来自 `test` 的 PR，不直接 push |
| `test` | 测试环境 | 功能分支直接 merge 进来；不直接在 `test` 上写代码 |

**CI**（`.github/workflows/ci.yml`）：push 到 `test`/`main`、以及 PR 到 `main` 时运行 ruff check → ruff format --check → mypy → pytest；PR 上另有 `Branch policy` 检查只允许 `test` → `main`。

**分支保护**（仓库暂时公开以使用免费 CI）：`main` 必须经 PR 合入，`Lint & test` 与 `Branch policy` 必须通过，对管理员同样生效；`test` 禁止强推与删除。将来改回私有后（免费版不支持分支保护），以上规则靠 CI 检查与自觉遵守，不要绕过。仓库公开期间，更要确保不提交任何密钥、账户号。
