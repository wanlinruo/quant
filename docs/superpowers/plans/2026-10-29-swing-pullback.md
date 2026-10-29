# swing_pullback 策略（研究与回测阶段）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现七姐妹 + 美光 + QQQ/PSQ 的均线回踩波段策略，并按设计文档第 7 节完成调参与样本外评估，产出研究报告。替换模拟盘策略不在本计划内（需用户另行确认）。

**Architecture:** 策略仍是无 I/O 的纯函数类。持仓状态（进场日、进场价、持有天数）由策略在每次运行时对最近 `replay_days` 个交易日确定性重放得到，回测与实盘行为一致。开高低收量与事件日历（财报、FOMC）通过 `Context` 注入；事件数据由数据层准备（EDGAR 8-K、美联储日历文件）。研究脚本在 Mac 上用 yfinance 数据跑有限参数组合。

**Tech Stack:** Python 3.12、pandas、numpy、现有 Backtester / SimBroker / metrics、pytest、ruff、mypy strict。

**Spec:** `docs/superpowers/specs/2026-10-29-swing-pullback-design.md`

## Global Constraints

- 策略只输出目标权重，纯计算无 I/O；回测与实盘同一个类。
- 只买入：个股、QQQ、PSQ；不做空、不用保证金；QQQ 与 PSQ 不同时持有。
- 标的：AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSLA、MU、QQQ、PSQ；PSQ 为 1 倍反向。
- 最多 4 个仓位（PSQ 占一个），基准权重 0.25，单仓上限 0.30，权重和 ≤ 1。
- 止损按收盘价判断；最长持有 30 个交易日；持仓个股在财报反应日前离场；FOMC 决议日不开新仓。
- 调参区间 2015-01-02 ~ 2022-12-30；样本外 2023-01-03 ~ 最新已收盘交易日；参数组合 ≤ 50 个，全部记录。
- 基准：持有 QQQ（主）、持有 SPY、8 只个股等权月度再平衡。
- 通过标准（样本外，且调参区间同方向）：夏普 > QQQ 且 ≥ 等权 8 只；最大回撤 ≤ QQQ 最大回撤 × 0.6；年化 ≥ QQQ 年化 × 0.8；相邻参数无断崖。
- 成本：`configs/base.yaml` 的老虎固定式套餐 + 滑点；业务时间美东；交易日 XNYS。
- Mac 上不调用老虎行情接口（会抢走服务器的行情权限）。

## Review Focus

- EDGAR `acceptanceDateTime` 的时区：若按 UTC 解读而实际为美东（或相反），盘后财报会被算成当日反应，财报前离场整体错一天 → Task 3 用 AAPL 2024-05-02 盘后公布（16:30 ET 左右）为夹具，断言反应日为 2024-05-03。
- 某标的在回测早期没有数据（META 2012 上市、PSQ 早期成交稀少）或某日缺行情 → 策略跳过该标的当日信号而非抛错；Task 5 测试缺列 / NaN 行。
- 拆股日（NVDA 2024-06-10、TSLA 2022-08-25）复权前后均线跳变 → 指标一律用复权后的开高低收，Task 1 的 field loader 测试断言拆股前后价格连续。
- 同一天多个进场信号 > 空余名额 → 按排序取前 N，结果确定（排序相同时按代码字母序）；Task 5 测试。
- 回测最后一个月份里财报日期缺失（EDGAR 尚未有未来日期）→ 未来没有记录视为“无已知财报”，不报错；Task 5 测试。

---

### Task 1: Context 注入开高低收量与事件；Backtester 支持；策略级调仓阈值

**Files:**
- Create: `src/quant/strategies/events.py`
- Modify: `src/quant/strategies/base.py`（Context、Strategy）
- Modify: `src/quant/engine/backtester.py`（构造参数、Context 构造、plan params）
- Modify: `src/quant/data/store.py`（`prices` 支持 `field="volume"`）
- Test: `tests/test_context_fields.py`

**Interfaces:**
- Produces:
  - `FieldLoader = Callable[[Sequence[str], str, date | None, date], pd.DataFrame]`（symbols, field, start, end → 宽表）
  - `EventCalendar(earnings: Mapping[str, tuple[date, ...]] = {}, fomc: tuple[date, ...] = ())`，frozen；方法 `next_earnings(symbol: str, after: date) -> date | None`（第一个 > after 的反应日）、`is_fomc(day: date) -> bool`
  - `Context` 新增字段 `field_loader: FieldLoader | None = None`、`events: EventCalendar = EventCalendar()`；方法 `Context.field(symbols, field, start=None, end=None) -> pd.DataFrame`，field ∈ {"open","high","low","close","volume"}，同样截断到 as_of、end > as_of 抛 `LookAheadError`；`field_loader` 为 None 时抛 `ValueError("…不提供开高低收…")`
  - `Strategy.min_weight_diff: ClassVar[float | None] = None`：非 None 时 Backtester 用它替换 `plan_params.min_weight_diff`
  - `field_loader_from_bars(bars: Mapping[str, pd.DataFrame]) -> FieldLoader`（`engine/backtester.py`，纯函数，复权口径同 `adjusted_ohlc`，volume 不复权）
  - `Backtester(..., events: EventCalendar | None = None)`

- [ ] **Step 1: 写失败测试** `tests/test_context_fields.py`
  - `test_field_truncated_to_as_of`：loader 返回到 as_of+5 的数据，`ctx.field(["AAPL"], "high")` 最后一行日期 == as_of。
  - `test_field_future_end_raises`：`ctx.field(..., end=as_of+1天)` 抛 `LookAheadError`。
  - `test_field_without_loader_raises`：默认 Context 调 `field` 抛 `ValueError`。
  - `test_field_loader_adjusted`：构造 close=100、adj_close=50 的 bars，`field_loader_from_bars` 的 "open"=原 open×0.5，"volume" 不变。
  - `test_split_continuity`：模拟 2:1 拆股（拆股前 close 200、adj_close 100，之后 close 101、adj 101），复权后 "close" 序列相邻比值在 0.95–1.05 之间。
  - `test_event_calendar`：earnings={"AAPL": (2024-05-03, 2024-08-02)}，`next_earnings("AAPL", 2024-05-03) == 2024-08-02`，`next_earnings("MSFT", …) is None`，`is_fomc` 对给定日期为真。
  - `test_backtester_passes_events_and_threshold`：一个记录 `ctx.events` 并返回固定权重的测试策略（`min_weight_diff = 0.5`），跑 Backtester 后断言收到的 events 即传入对象，且价格漂移不触发再平衡（交易次数 == 1）。
  - `test_store_prices_volume`：`ParquetStore.prices(["SPY"], field="volume")` 返回整数成交量。

- [ ] **Step 2: 运行确认失败** — Run: `TRADING_MODE=sim uv run pytest tests/test_context_fields.py -v` — Expected: FAIL（ImportError / AttributeError）

- [ ] **Step 3: 实现** 上述接口；`Context.__post_init__` 像现有 `loader` 一样包装 `field_loader` 做截断；Backtester 在每次 `Context(...)` 构造时传入 `field_loader_from_bars(bars)` 的结果（构造时算一次）与 `events`。

- [ ] **Step 4: 运行确认通过** — Run: `TRADING_MODE=sim uv run pytest tests/test_context_fields.py tests/test_backtester.py tests/test_etf_momentum.py -v` — Expected: PASS（现有策略行为不变）

- [ ] **Step 5: Commit** — `git commit -m "feat(strategy): Context 提供开高低收量与事件日历"`

---

### Task 2: FOMC 决议日数据文件

**Files:**
- Create: `src/quant/data/fomc_dates.csv`（列 `date`；首行注释写来源 URL 与整理日期）
- Create: `src/quant/data/events.py`
- Test: `tests/test_events_data.py`

**Interfaces:**
- Produces: `load_fomc_dates(path: Path | None = None) -> tuple[date, ...]`（默认读包内 csv，升序、去重）

- [ ] **Step 1: 写失败测试**
  - `test_fomc_known_dates`：结果包含 2022-06-15、2024-09-18、2025-12-10、2026-10-28。
  - `test_fomc_range_sorted`：最早 ≤ 2014-01-29，最晚 ≥ 2027-01-27；严格递增；每年 8 次（2014–2026 各年计数 == 8）。
- [ ] **Step 2: 运行确认失败** — `TRADING_MODE=sim uv run pytest tests/test_events_data.py -v` → FAIL
- [ ] **Step 3: 整理数据** 从 https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm 与 `fomchistorical{年}.htm` 页面取 2014–2027 年**例会**决议日（两日会议取第二天），不含临时会议；实现 `load_fomc_dates`（`importlib.resources` 读取）。
- [ ] **Step 4: 运行确认通过** — 同上 → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(data): FOMC 决议日数据"`

---

### Task 3: 财报反应日（SEC EDGAR 8-K Item 2.02）

**Files:**
- Create: `src/quant/data/providers/edgar.py`
- Modify: `src/quant/data/events.py`（读写 earnings.csv）
- Modify: `src/quant/cli.py`（子命令 `events sync-earnings`）
- Test: `tests/test_edgar.py`（夹具 JSON 写在测试内，不访问网络）

**Interfaces:**
- Produces:
  - `CIKS: dict[str, tuple[int, ...]]`：AAPL 320193；MSFT 789019；NVDA 1045810；GOOGL (1652044, 1288776)（Alphabet 与 2015 年前的 Google Inc.）；AMZN 1018724；META 1326801；TSLA 1318605；MU 723125
  - `parse_earnings_filings(submissions: Mapping[str, Any]) -> list[datetime]`：取 form == "8-K" 且 items 含 "2.02" 的 acceptance 时间（带时区，美东）
  - `reaction_day(accepted: datetime, cal: TradingCalendar) -> date`：美东时间 ≥ 当日收盘（含半日市）或非交易日 → 下一交易日；< 开盘 → 当日；盘中 → 当日
  - `fetch_earnings(symbol: str, user_agent: str, fetch: Callable[[str], Mapping[str, Any]] = ...) -> list[date]`：读 `https://data.sec.gov/submissions/CIK{cik:010d}.json` 及其 `filings.files` 中的历史分页，2014 年起，去重排序；请求间隔 ≥ 0.15 秒
  - `save_earnings(path: Path, data: Mapping[str, Sequence[date]]) -> None` / `load_earnings(path: Path) -> dict[str, tuple[date, ...]]`，文件 `{data_dir}/events/earnings.csv`，列 `symbol,reaction_day`
  - CLI：`uv run quant events sync-earnings --user-agent "<名字 邮箱>"`

- [ ] **Step 1: 写失败测试**
  - `test_parse_items`：夹具含 8-K(items "2.02,9.01")、8-K(items "5.02")、10-Q，只返回第一条。
  - `test_reaction_day_after_close`：AAPL 2024-05-02 16:30 ET 受理 → 2024-05-03（**先核实 EDGAR acceptanceDateTime 的实际时区**，夹具按核实结果写，测试注释写明依据）。
  - `test_reaction_day_pre_open`：07:00 ET → 当日；周六受理 → 下周一；半日市 2026-11-27 13:30 ET → 2026-11-30。
  - `test_fetch_follows_history_files`：fake fetch 返回 recent + 1 个历史分页，结果合并、去重、按日期升序，2014 年前的被过滤。
  - `test_save_load_roundtrip`。
- [ ] **Step 2: 运行确认失败** — `TRADING_MODE=sim uv run pytest tests/test_edgar.py -v` → FAIL
- [ ] **Step 3: 实现**；User-Agent 必须由参数提供（SEC 要求声明联系方式），不写死在代码里。
- [ ] **Step 4: 运行确认通过** → PASS
- [ ] **Step 5: 真实同步一次**（Mac）：`uv run quant events sync-earnings --user-agent "quant-research <用户邮箱>"`；抽查 AAPL、NVDA 最近 4 个反应日与公开财报日一致；GOOGL 2014–2015 有记录。
- [ ] **Step 6: Commit** — `git commit -m "feat(data): EDGAR 财报反应日"`

---

### Task 4: 指标与进出场判定（纯函数）

**Files:**
- Create: `src/quant/strategies/swing_rules.py`
- Test: `tests/test_swing_rules.py`

**Interfaces:**
- Produces（输入均为单标的 `pd.Series`，按日期索引，复权口径；返回同索引的 bool/float Series）：
  - `sma(x: pd.Series, n: int) -> pd.Series`；`rsi(close: pd.Series, n: int) -> pd.Series`（Wilder）
  - `ConfirmMode = Literal["close_strength", "volume", "rsi2"]`
  - `long_setup(o, h, l, c, v, *, trend_ma: int, slope_days: int, support_ma: int, tol: float, touch_days: int, confirm: ConfirmMode) -> pd.Series[bool]`
    - 大方向：`c > sma(c, trend_ma)`，且 `slope_days > 0` 时 `sma[t] > sma[t-slope_days]`
    - 回踩：`l` 近 `touch_days` 日最低 ≤ `sma(c, support_ma) × (1 + tol)`，且 `c ≥ sma(c, support_ma) × (1 − tol)`
    - 确认：close_strength = `(c−l)/(h−l) ≥ 0.6` 且 `c > c.shift(1)`；volume = `v ≥ 1.5 × v 的 20 日均值` 且 `c > o`；rsi2 = `rsi(c,2).shift(1) < 10` 且 `c > c.shift(1)`
  - `short_setup(o, h, l, c, *, trend_ma, support_ma, tol, touch_days) -> pd.Series[bool]`（用于 QQQ → 买 PSQ）：`c < sma(c, trend_ma)`；近 `touch_days` 日最高 ≥ `sma(c, support_ma) × (1 − tol)`；`c < sma(c, support_ma)`；`c < o` 或 `(c−l)/(h−l) ≤ 0.4`
  - 指标预热不足处为 False，不抛错。

- [ ] **Step 1: 写失败测试**：用手工构造的 260 日序列
  - `test_long_setup_triggers_on_pullback`：上升趋势中第 250 日回踩 20 日线并收阳 → 该日 True，前后非回踩日 False。
  - `test_long_setup_requires_uptrend`：同样形态但处于 120 日线下方 → False。
  - 每个 confirm 模式各一个正例一个反例（`test_confirm_close_strength`、`test_confirm_volume`、`test_confirm_rsi2`）。
  - `test_short_setup_rejection`：下跌趋势中反抽 20 日线后收阴 → True。
  - `test_warmup_false_and_nan_safe`：前 119 日全 False；中间插入一行 NaN 不抛错且该行 False。
  - `test_rsi_known_values`：RSI(2) 对一段固定序列的值与手算一致（容差 1e-9）。
- [ ] **Step 2: 运行确认失败** → FAIL
- [ ] **Step 3: 实现**（向量化）
- [ ] **Step 4: 运行确认通过** → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(strategy): 波段进出场判定"`

---

### Task 5: SwingPullback 策略（重放状态、仓位、事件）

**Files:**
- Create: `src/quant/strategies/swing_pullback.py`
- Modify: `src/quant/strategies/__init__.py`（注册 `swing_pullback`）
- Test: `tests/test_swing_pullback.py`

**Interfaces:**
- Consumes: Task 1 的 `Context.field`、`EventCalendar`；Task 4 的 `sma`、`long_setup`、`short_setup`
- Produces:
  - `SwingParams.from_mapping(params) -> SwingParams`（frozen），字段与默认值：`stocks=("AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","MU")`、`index="QQQ"`、`inverse="PSQ"`、`trend_ma=120`、`slope_days=0`、`support_ma=20`、`tol=0.01`、`touch_days=3`、`confirm="close_strength"`、`exit_ma=5`、`arm_pct=0.02`、`stop_pct=0.07`、`support_break=0.03`、`max_hold=30`、`max_positions=4`、`base_weight=0.25`、`max_weight=0.30`、`vol_window=20`、`earnings_exit_days=2`、`earnings_no_entry_days=5`、`fomc_exit=False`、`replay_days=250`；未知键抛 `ValueError`
  - `SwingPullback(Strategy)`：`name="swing_pullback"`、`min_weight_diff=0.10`、`symbols(params)` = stocks + index + inverse、`warmup_days(params) = trend_ma + replay_days + 30`、`target_weights(ctx) -> dict[str, float]`
  - `replay(...) -> ReplayState`（模块级纯函数，供测试）：`ReplayState.positions: dict[str, Holding]`，`Holding(entry_day: date, entry_price: float, weight: float, days_held: int, armed: bool)`

**重放规则**（对 as_of 之前最近 `replay_days` 个交易日逐日执行，信号日 t 的进出场在 t+1 开盘价成交）：
1. 离场（对每个持仓，按 t 收盘判断，任一满足即离场）：`days_held ≥ max_hold`；`c ≤ entry_price × (1 − stop_pct)`；`c < sma(support_ma) × (1 − support_break)`；`armed` 且 `c < sma(exit_ma)`（`armed` = 进场后任一收盘 ≥ entry_price × (1 + arm_pct)）；个股的 `next_earnings(symbol, t)` 在 t 之后 `earnings_exit_days` 个交易日内；`fomc_exit` 且 t+1 是 FOMC 决议日。PSQ 的离场另加：QQQ 收盘 > `sma(QQQ, exit_ma)`，此时不看 armed。
2. 进场（空余名额 > 0，且 t+1 不是 FOMC 决议日）：候选 = `long_setup` 为真的个股与 QQQ（且 `next_earnings` 不在 `earnings_no_entry_days` 个交易日内）+ `short_setup(QQQ)` 为真时的 PSQ；排除已持有、QQQ 与 PSQ 互斥；按 `c / sma(trend_ma) − 1`（PSQ 用 QQQ 的 `1 − c / sma(trend_ma)`）降序、同值按代码升序，取前“空余名额”个。
3. 权重：`w_i = min(max_weight, base_weight × v̄ / v_i)`，`v_i` 为该标的 `vol_window` 日收益标准差，`v̄` 为当日所有有数据的候选与持仓标的的均值；进场时确定，持有期间不变；权重和 > 1 时按比例缩放新进场仓位。
4. `target_weights` 返回 as_of 收盘后的持仓（含当日新进场、去掉当日离场）及其权重。

- [ ] **Step 1: 写失败测试**（合成数据 + 手工 EventCalendar）
  - `test_entry_then_trend_exit`：一只股票回踩后上涨 3% 再跌破 5 日线 → 进场日出现权重、跌破后消失；持有期间权重不变。
  - `test_stop_loss`：进场后收盘跌 8% → 离场。
  - `test_max_hold`：横盘不触发任何离场 → 第 30 个交易日后离场。
  - `test_earnings_exit_and_no_entry`：反应日为 t+2 → 当日离场；反应日在 4 个交易日内 → 不进场；`earnings` 中没有未来日期 → 正常进场。
  - `test_fomc_no_entry`：t+1 为 FOMC → 当日不进场，次日可进场。
  - `test_max_positions_and_ranking`：6 只同时触发 → 只持 4 只，为偏离 120 日线最大的 4 只；并列按字母序。
  - `test_qqq_psq_exclusive`：QQQ 持有期间 PSQ 信号不进场；PSQ 在 QQQ 收复 5 日线后离场。
  - `test_weight_caps`：高波动股票权重 < 0.25，低波动股票 ≤ 0.30，总和 ≤ 1。
  - `test_missing_symbol_data`：META 列在早期全为 NaN → 不抛错，META 不出现在权重中。
  - `test_no_look_ahead`：用截断到 as_of 的数据与完整数据分别运行，结果相同。
  - `test_registered`：`get_strategy("swing_pullback")` 可用；`SwingParams.from_mapping({"bogus": 1})` 抛 ValueError。
- [ ] **Step 2: 运行确认失败** → FAIL
- [ ] **Step 3: 实现**；重放所需的指标在一次运行中对整段窗口向量化计算一次。
- [ ] **Step 4: 运行确认通过** — `TRADING_MODE=sim uv run pytest tests/test_swing_pullback.py -v` → PASS
- [ ] **Step 5: 性能检查**：真实数据 2015–2026 回测 1 次 ≤ 120 秒（`time uv run quant backtest --strategy swing_pullback --start 2015-01-02 --no-db`，需 Task 1 events 为空时可运行）。超出时优化重放（例如按 as_of 缓存上一日状态，前提是结果与不缓存一致并有测试）。
- [ ] **Step 6: Commit** — `git commit -m "feat(strategy): swing_pullback 策略"`

---

### Task 6: 交易明细、等权基准与通过标准判定

**Files:**
- Create: `src/quant/engine/trades.py`、`src/quant/engine/evaluation.py`
- Modify: `src/quant/engine/metrics.py`（等权基准）
- Test: `tests/test_trades_evaluation.py`

**Interfaces:**
- Produces:
  - `round_trips(fills: Sequence[Fill]) -> pd.DataFrame`：按标的 FIFO 配对，列 `symbol, entry_day, exit_day, qty, entry_price, exit_price, pnl, return, holding_days`（交易日数），含费用；未平仓不计入
  - `trade_stats(trips: pd.DataFrame) -> dict[str, float]`：`win_rate`、`payoff`（平均盈利 / 平均亏损绝对值）、`avg_holding_days`、`count`
  - `equal_weight_rebalanced(prices: pd.DataFrame, initial: float = 1.0) -> pd.Series`：每月最后一个交易日收盘按等权再平衡，无成本
  - `Criteria(sharpe_vs_qqq: bool, sharpe_vs_equal: bool, drawdown: bool, cagr: bool)`，`passed` 属性；`meets_criteria(strategy: Metrics, qqq: Metrics, equal: Metrics) -> Criteria`，数值：回撤 `strategy.max_drawdown ≥ qqq.max_drawdown × 0.6`（回撤为负数），年化 `≥ qqq.cagr × 0.8`

- [ ] **Step 1: 写失败测试**
  - `test_round_trips_fifo`：买 10 → 买 10 → 卖 15 → 卖 5，得到两笔配对，盈亏与持有天数正确，费用计入。
  - `test_trade_stats`：3 盈 1 亏的固定样例。
  - `test_equal_weight_rebalanced`：两只资产一只翻倍一只不变，月末再平衡后净值与手算一致。
  - `test_meets_criteria_boundaries`：恰好等于阈值时通过（≥），差一点不通过；QQQ cagr 为负时 80% 规则的方向正确（策略需 ≥ qqq.cagr × 0.8 的数值比较照字面）。
- [ ] **Step 2: 运行确认失败** → FAIL
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过** → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(engine): 交易明细、等权基准、通过标准"`

---

### Task 7: 研究脚本与报告

**Files:**
- Create: `scripts/research/swing_pullback.py`
- Create: `docs/research/2026-10-29-swing-pullback.md`（脚本生成后提交）
- Test: `tests/test_research_grid.py`（只测参数网格与选择逻辑，不跑真实回测）

**Interfaces:**
- Consumes: Task 1–6 全部
- Produces:
  - `GRID: list[dict[str, object]]`：`trend_ma ∈ {120, 50}` × `support_ma ∈ {20, 30, 50}` × `confirm ∈ {"close_strength","volume","rsi2"}` × `exit_ma ∈ {5, 10}` = 36 组；之后只对调参区间最优组合追加 `fomc_exit=True` 与 `slope_days=10` 各 1 组（共 ≤ 38 组）
  - `neighbors(best: Mapping[str, object]) -> list[dict[str, object]]`：在 GRID 中与 best 恰有一个维度不同的组合
  - `select_best(results: pd.DataFrame) -> dict[str, object]`：按调参区间夏普最高选；并列取最大回撤较小者

- [ ] **Step 1: 写失败测试** `test_grid_size`（== 36、键集合正确）、`test_neighbors`（best 为 (120,20,close_strength,5) 时邻居数 == 1+2+2+1 = 6）、`test_select_best_tiebreak`。
- [ ] **Step 2: 运行确认失败** → FAIL
- [ ] **Step 3: 实现脚本**：`ensure_bars`（yfinance）准备 10 只 + SPY 自 2014-01-02 起的数据；`load_earnings` + `load_fomc_dates` 构造 EventCalendar；每组参数分别跑调参区间与样本外；基准 QQQ、SPY、8 只等权；输出报告：全部组合结果表（含失败）、最优组合、邻居稳健性表、样本外对照与 `meets_criteria` 逐条结果、逐年收益、交易统计、已知局限（幸存者偏差、日线近似“大单托举”、EDGAR 反应日可能误差）。
- [ ] **Step 4: 运行测试与脚本** — `TRADING_MODE=sim uv run pytest tests/test_research_grid.py -v` → PASS；`TRADING_MODE=sim uv run python scripts/research/swing_pullback.py` 生成报告且无异常。
- [ ] **Step 5: 全量检查** — `make check`（含 PostgreSQL 的测试用本地 `QUANT_TEST_DATABASE_URL`）→ 全部通过
- [ ] **Step 6: Commit** — `git commit -m "docs(research): swing_pullback 调参与样本外评估"`

---

完成后：把报告结论（是否通过、主要数字、局限）告诉用户。是否替换模拟盘策略由用户决定；替换涉及白名单、单仓上限、数据任务标的、服务器端财报日期来源（老虎财报日历）与切换日调仓，另立计划。
