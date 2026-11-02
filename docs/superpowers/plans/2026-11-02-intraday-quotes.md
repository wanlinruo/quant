# 盘中行情（第一阶段：常规时段，Finnhub）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 面板在常规交易时段显示指数与持仓的盘中价格、当日涨跌与持仓当日盈亏；盘前、盘后、夜盘显示所处时段并注明“扩展时段行情未接入，显示上一交易日收盘”。只用于查看，交易继续只用日线。

**Spec:** #93（用户 2026-11-02 决定：只看盘、不影响交易；第一阶段零成本用 Finnhub 常规时段；第二阶段视是否购买老虎 Nasdaq Basic 再接扩展时段）。

## Global Constraints

- 与交易链路隔离：新服务不加载老虎配置、不导入 `tigeropen` / `quant.broker` / 老虎数据源；策略、信号、执行器不改。
- 面板只读：新接口只有 GET。
- Finnhub 免费档 60 次/分钟：盘中每轮最多 50 个标的，两次请求间隔 ≥ 1.1 秒；`market_extras`、`stock_data` 在 21:00 ET 以后运行，与盘中不重叠。
- 业务时间美东；交易日、半日市按 XNYS。表结构经 Alembic。

## 决定

- **时段** `us_session(cal, now)`：盘前 04:00–开盘；常规 开盘–收盘（半日市提前收盘）；盘后 收盘–收盘 + 4 小时；夜盘 盘后结束–次日 04:00（次日为交易日时，含周日晚）；其余为休市。
- **标的**：SPY、QQQ、DIA、IWM + 各账户最新持仓 + `quotes.watchlist`（配置，默认空），去重后最多 50 个（指数与持仓优先）。
- **采集**：只在常规时段，每 60 秒一轮；`/quote` 的 `t` 为 0 或价格无效时跳过该标的。失败只记日志，不告警为交易故障。
- **存储**：`intraday_quotes`（symbol 主键，price、prev_close、change_pct、quote_time、fetched_at），每只只留最新一条。
- **接口** `GET /api/market/intraday`：`session`、`session_text`、`as_of`（最新报价时间）、`stale`（常规时段内最新报价超过 5 分钟）、`note`、`quotes`。不在常规时段时 `quotes` 为空，`note` 说明扩展时段未接入。
- **前端**：首页“盘中行情”面板：指数一行；持仓表（盘中价、今日涨跌、当日盈亏 = 股数 ×（盘中价 − 昨收））；每 60 秒刷新；标注“Finnhub · 仅供查看，交易用收盘日线”。

## Review Focus

1. 半日市：13:00 收盘后进入盘后，17:00 后进入夜盘或休市。
2. 周末与节假日：周六全天休市；周日 20:00 后为夜盘（周一是交易日时）；节假日前一晚不是夜盘。
3. Finnhub 限流或断网：本轮跳过，不退出；数据过期时 `stale=true`，面板显示“行情延迟”。
4. 持仓超过 50 只：截断，指数与持仓优先。
5. 隔离：导入 `quant.jobs.quotes` 后 `sys.modules` 中没有 `tigeropen`。

### Task 1: 时段函数 + Finnhub `quote()`（`market/session.py`、`data/providers/finnhub.py`）
### Task 2: 表、迁移、`jobs/quotes.py` 采集服务、compose
### Task 3: 接口 `GET /api/market/intraday`
### Task 4: 前端盘中行情面板
### Task 5: `make check`、终审；首次调仓核对后部署；常规时段在服务器实测
