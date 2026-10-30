# 组合策略 combo（研究与回测阶段）实施计划

> **给执行者：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐个实施本计划。步骤用复选框（`- [ ]`）跟踪进度。

**目标：** 实现 `combo` 策略（40% 动量选股 + 60% 七姐妹 V2 + QQQ 波动率择时），用回测引擎在标普 500 历史成分股上正式评估，输出研究报告。

**架构：** 新增两个纯计算策略类：`momentum_select`（月度动量选股）和 `combo`（组合动量、`swing_pullback` 与波动率择时，输出合并后的目标权重）。“当时的成分股”作为纯数据对象 `Constituents` 经 `Context.universe` 注入。研究脚本在 Mac 上用 yfinance 与 fja05680/sp500 历史成分名单跑 10 组回测并按设计第 7 节判定。

**技术栈：** Python 3.12、pandas、numpy、uv、pytest、ruff、mypy strict。

**设计文档：** `docs/superpowers/specs/2026-10-30-momentum-combo-design.md`

**范围说明：** 单只上限 30%（风控 `max_symbol_weight`）属于风控配置，随替换上线一起改；按 40/60 与 V2 单仓上限 30%，合并后单只最多约 0.4 × 0.25 + 0.6 × 0.3 = 28%。另： 本计划覆盖设计第 4 节（规则）、5.1（回测数据）、第 6 节中策略 / Context / 研究脚本部分、第 7 节（评估）。**实盘数据通路**（新浪月度批量排序、成分股月度快照任务、动态白名单、`max_symbol_weight`、老虎额度核实、实盘财报日、`jobs/daily.py` 为策略提供 `field_loader` / `events` / `universe`）放到第二份计划，在研究通过且用户确认替换后再写——研究不通过时这些工作都用不上。

## 全局约束

- 策略只输出目标权重，纯计算、无 I/O；回测与实盘同一个类（CLAUDE.md）。
- 只买入；不用 PSQ / 反向 ETF、杠杆、期权、保证金。
- 比例 40/60；波动率择时：QQQ 20 日收益率标准差 × √252 > 30% 时全部目标权重 × 0.5；当日收盘计算，次日执行，无滞后带。
- 动量仓：每月最后一个交易日收盘后计算；当时的标普 500 成分股；收盘价 > 5；≥ 252 个交易日历史；60 日平均成交额前 200；收盘 > 120 日均线；20 日平均成交额 > 120 日平均成交额；按 126 日涨幅取前 4，等权；不足 4 只时空位留现金。
- V2 仓：`trend_ma=120, slope_days=10, support_ma=20, confirm=close_strength, exit_ma=10`，不用 PSQ。
- 动量仓财报处理两种都跑：(a) 持有过财报；(b) 财报前 2 个交易日卖出、当月不补回。
- 区间 2015-01-02 至最近已收盘交易日；调参区间 2015–2022、样本外 2023 至今分别报告。
- 通过标准（全区间，扣除成本）：最大回撤 ≥ -25%；年化 ≥ QQQ 年化 + 3 个百分点；夏普 ≥ 1.1 且两个区间夏普都高于 QQQ；相邻设置（比例 30/70、50/50；阈值 25%、35%；只数 3、5）最大回撤 ≥ -30% 且夏普 ≥ 1.0。
- 业务时间用美东时间；交易日以 XNYS 日历为准。
- 不提交密钥、账户号；SEC User-Agent 只经命令行参数传入，不写入仓库和日志。
- 测试统一用 `TRADING_MODE=sim`；`make check` 与 CI 相同。

## 评审重点（测试之外最容易出问题的输入）

1. **成分股代码与 Yahoo 代码不一致**（`BRK.B` vs `BRK-B`）：下载不能静默失败，`BRK.B` 应能拿到数据——任务 2 的测试固定这一点。
2. **月末当天的 as_of**：as_of 恰为月末交易日时应使用当天的新排名（次日调仓），而不是上个月的——任务 4 测试。
3. **成分股名单早于第一条记录的日期**：`members()` 应报错，而不是返回空集合导致静默空仓——任务 1 测试。
4. **择时数据不足**（QQQ 不足 21 个收盘价或含 NaN）：系数应为 1（不减仓），而不是 NaN 让权重校验失败——任务 5 测试。
5. **两仓同一标的**（如 MU 同时在动量和 V2）：权重相加而不是覆盖——任务 5 测试。

---

### 任务 1： 成分股数据对象与 Context 注入

**文件：**
- 新建： `src/quant/strategies/universe.py`、`src/quant/data/universe.py`
- 修改： `src/quant/strategies/base.py`（`Context` 增加字段）、`src/quant/engine/backtester.py`（`Backtester.__init__` 增加 `universe` 参数并传入 Context）
- 测试： `tests/test_universe.py`

**接口：**
- 提供：
  - `quant.strategies.universe.Constituents(snapshots: Mapping[date, frozenset[str]])`（frozen dataclass，可 pickle）
    - `members(d: date) -> frozenset[str]`：日期 ≤ d 的最近一条快照；d 早于第一条时抛 `ValueError`。
    - `all_symbols(start: date, end: date) -> frozenset[str]`：区间内（含 start 当时生效的那条）出现过的全部代码。
  - `quant.data.universe.parse_sp500_history(text: str) -> Constituents`：解析 fja05680 CSV（列 `date,tickers`，tickers 为逗号分隔字符串）。
  - `quant.data.universe.load_constituents(path: Path) -> Constituents`
  - `Context.universe: Constituents | None = None`；`Backtester(..., universe: Constituents | None = None)`。

- [ ] **步骤 1： 写失败的测试**

```python
TEXT = 'date,tickers\n2015-01-02,"AAA,BBB,CCC"\n2015-03-02,"AAA,CCC,DDD"\n'

def test_members_uses_latest_snapshot_on_or_before() -> None:
    u = parse_sp500_history(TEXT)
    assert u.members(date(2015, 1, 2)) == {"AAA", "BBB", "CCC"}
    assert u.members(date(2015, 3, 1)) == {"AAA", "BBB", "CCC"}
    assert u.members(date(2015, 3, 2)) == {"AAA", "CCC", "DDD"}

def test_members_before_first_snapshot_raises() -> None:
    with pytest.raises(ValueError, match="成分股"):
        parse_sp500_history(TEXT).members(date(2014, 12, 31))

def test_all_symbols_includes_snapshot_in_effect_at_start() -> None:
    u = parse_sp500_history(TEXT)
    assert u.all_symbols(date(2015, 2, 1), date(2015, 12, 31)) == {"AAA", "BBB", "CCC", "DDD"}

def test_constituents_pickles() -> None:
    u = parse_sp500_history(TEXT)
    assert pickle.loads(pickle.dumps(u)).members(date(2015, 3, 2)) == u.members(date(2015, 3, 2))

def test_backtester_passes_universe_to_context() -> None:
    # 用一个记录 ctx.universe 的桩策略跑 3 个交易日的回测，断言收到的就是传入的对象
```

- [ ] **步骤 2： 运行，确认失败**

运行：`TRADING_MODE=sim uv run pytest tests/test_universe.py -v`
预期：测试失败（`ModuleNotFoundError: quant.strategies.universe`）

- [ ] **步骤 3： 实现上述接口**；`Context.universe` 只是透传字段，不做截断（成分股快照本身按日期查询）。

- [ ] **步骤 4： 运行通过**，再跑 `TRADING_MODE=sim uv run pytest -q` 确认原有测试不受影响。

- [ ] **步骤 5： 提交** `feat: 成分股快照数据对象并注入 Context（Refs #47）`

---

### 任务 2： 大范围标的的数据获取

**文件：**
- 修改： `src/quant/data/providers/yfinance.py`（下载时把代码中的 `.` 换成 `-`）、`src/quant/data/providers/edgar.py`
- 测试： `tests/test_providers.py`、`tests/test_edgar.py`

**接口：**
- 提供：
  - `YFinanceProvider.daily_bars("BRK.B", ...)` 向 yfinance 请求 `BRK-B`，返回数据照常（存储仍用 `BRK.B`）。
  - `edgar.fetch_cik_map(user_agent: str, fetch: Fetch | None = None) -> dict[str, int]`：读 `https://www.sec.gov/files/company_tickers.json`，键为代码（`-` 换成 `.`）。
  - `edgar.fetch_earnings(symbol, user_agent, fetch=None, sleep=..., cal=None, ciks: Sequence[int] | None = None)`：`ciks` 为 None 时沿用 `CIKS[symbol]`。

- [ ] **步骤 1： 写失败的测试**

```python
def test_yfinance_maps_dot_ticker() -> None:
    seen = []
    def fake_download(**kw):
        seen.append(kw["tickers"]); return <单日合法的 OHLCV DataFrame>
    YFinanceProvider(downloader=fake_download).daily_bars("BRK.B", date(2024, 1, 2), date(2024, 1, 2))
    assert seen == ["BRK-B"]

def test_fetch_cik_map() -> None:
    payload = {"0": {"cik_str": 1067983, "ticker": "BRK-B", "title": "x"},
               "1": {"cik_str": 320193, "ticker": "AAPL", "title": "y"}}
    assert edgar.fetch_cik_map("ua", fetch=lambda url: payload) == {"BRK.B": 1067983, "AAPL": 320193}

def test_fetch_earnings_with_explicit_ciks() -> None:
    # 复用 test_edgar 现有的 8-K 假数据，传 ciks=(123,)、symbol="ZZZ"（不在 CIKS 中），应正常返回反应日
```


- [ ] **步骤 2： 运行，确认失败**

运行：`TRADING_MODE=sim uv run pytest tests/test_providers.py tests/test_edgar.py -v`
预期：新增 3 个用例失败

- [ ] **步骤 3： 实现**

- [ ] **步骤 4： 运行通过**

- [ ] **步骤 5： 提交** `feat: yfinance 代码映射与 SEC 代码 → CIK 映射（Refs #47）`

---

### 任务 3： swing_pullback 可关闭 PSQ

**文件：**
- 修改： `src/quant/strategies/swing_pullback.py`
- 测试： `tests/test_swing_pullback.py`

**接口：**
- 提供： `SwingParams.use_inverse: bool = True`。为 False 时：`symbols` 不含 PSQ；`simulate` 不产生 PSQ 候选；`compute_inputs` 在缺少 PSQ 数据时正常运行。默认行为不变。

- [ ] **步骤 1： 写失败的测试**

```python
def test_inverse_disabled() -> None:
    p = replace(P, use_inverse=False)
    assert "PSQ" not in p.symbols
    inp = make_inputs()
    inp.short_sig.loc[IDX[10]] = True
    inp.score.loc[:, "PSQ"] = 0.5
    assert "PSQ" not in held(inp, 12, params=p)
    assert "PSQ" in held(inp, 12)  # 默认仍会买 PSQ
```

再加一个用例：`compute_inputs(frames, p)`，frames 中没有 PSQ 列，`use_inverse=False` 时不报错。

- [ ] **步骤 2： 运行，确认失败**

运行：`TRADING_MODE=sim uv run pytest tests/test_swing_pullback.py -v -k inverse`
预期：测试失败（`SwingParams` 没有 `use_inverse`）

- [ ] **步骤 3： 实现**

- [ ] **步骤 4： 运行 `tests/test_swing_pullback.py` 全部通过**

- [ ] **步骤 5： 提交** `feat: swing_pullback 增加 use_inverse 参数（Refs #47）`

---

### 任务 4： momentum_select 策略

**文件：**
- 新建： `src/quant/strategies/momentum_select.py`
- 修改： `src/quant/strategies/__init__.py`（注册）
- 测试： `tests/test_momentum_select.py`

**接口：**
- 依赖： `Constituents`（任务 1）、`Context.universe`、`Context.field`、`Context.events`
- 提供：
  - `MomentumParams`（frozen dataclass，`from_mapping` 拒绝未知参数，风格同 `SwingParams`）：`top_n=4, liquidity_top=200, min_price=5.0, min_history=252, trend_ma=120, lookback=126, dv_window=60, dv_short=20, dv_long=120, earnings_exit=False, earnings_exit_days=2`
  - `rank_momentum(close: pd.DataFrame, volume: pd.DataFrame, members: Collection[str], p: MomentumParams) -> list[str]`：输入为截至排序日（最后一行）的宽表；返回全部合格代码，按 `lookback` 日涨幅降序，同分按代码升序。过滤顺序：成员且有列 → 非空收盘数 ≥ `min_history` → 最后收盘 > `min_price` → `dv_window` 日平均成交额（收盘 × 成交量）非空，取前 `liquidity_top` → 最后收盘 > `trend_ma` 日均值 → `dv_short` 日均成交额 > `dv_long` 日均成交额 → 涨幅非空。价格用 `Context.field` 的含分红复权口径（与回测一致；实盘口径在第二份计划处理）。
  - `rebalance_day(cal: TradingCalendar, d: date) -> date`：d 是月末交易日时返回 d，否则返回上个月的最后一个交易日。
  - `MomentumSelect(Strategy)`：`name = "momentum_select"`，`min_weight_diff = 0.10`，`max_daily_move = 0.6`。`target_weights`：取 `rebalance_day(as_of)` 的成员与排名前 `top_n`，各 `1 / top_n`；`earnings_exit=True` 时去掉满足 `rebalance_day < r ≤ as_of 之后第 earnings_exit_days 个交易日` 的标的（r 为 `events.next_earnings(s, rebalance_day)`）。按 `(rebalance_day, params)` 在实例内缓存排名（纯计算的记忆化）。`ctx.universe` 为 None 时抛 `ValueError`。`symbols(params)` 返回 `[]`（候选池随时间变化，由调用方按 `Constituents.all_symbols` 准备数据）；`warmup_days` 返回 `min_history + 30`。

- [ ] **步骤 1： 写失败的测试**（合成数据：300 个交易日，6 只股票）

```python
# 构造：A 涨 60%、B 涨 40%、C 涨 20%（都放量、在 120 日线上）；
# D 涨 80% 但不是成员；E 涨 50% 但收盘 4 元；F 涨 70% 但最近 20 日缩量
def test_rank_filters_and_order() -> None:
    assert rank_momentum(close, volume, {"A", "B", "C", "E", "F"}, P) == ["A", "B", "C"]

def test_rank_liquidity_cut() -> None:
    # liquidity_top=2，成交额 A < B < C：A 被流动性过滤掉
    assert rank_momentum(close, volume2, {"A", "B", "C"}, replace(P, liquidity_top=2)) == ["B", "C"]

def test_rank_requires_history() -> None:
    # B 只有 200 个交易日数据
    assert "B" not in rank_momentum(close_short_b, volume, {"A", "B", "C"}, P)

def test_rebalance_day() -> None:
    assert rebalance_day(CAL, date(2024, 5, 31)) == date(2024, 5, 31)
    assert rebalance_day(CAL, date(2024, 6, 3)) == date(2024, 5, 31)
    assert rebalance_day(CAL, date(2024, 6, 28)) == date(2024, 6, 28)

def test_target_weights_equal_and_capped_by_top_n() -> None:
    # top_n=2 → {"A": 0.5, "B": 0.5}；top_n=4 但只有 3 只合格 → 各 0.25，总和 0.75

def test_month_end_uses_new_ranking() -> None:
    # 让排名在 5-31 发生变化：as_of=5-31 用 5-31 的排名，as_of=5-30 用 4-30 的排名

def test_earnings_exit() -> None:
    # A 的反应日为 6 月第 10 个交易日：earnings_exit=False 时整月持有；
    # earnings_exit=True 时，as_of 为第 8 个交易日起不再含 A，到月底也不补回

def test_requires_universe() -> None:
    with pytest.raises(ValueError, match="成分股"):
        MomentumSelect().target_weights(<universe=None 的 Context>)
```

- [ ] **步骤 2： 运行，确认失败**

运行：`TRADING_MODE=sim uv run pytest tests/test_momentum_select.py -v`
预期：测试失败（模块不存在）

- [ ] **步骤 3： 实现**

- [ ] **步骤 4： 运行通过；`uv run mypy src` 无报错**

- [ ] **步骤 5： 提交** `feat: momentum_select 月度动量选股策略（Refs #47）`

---

### 任务 5： combo 策略

**文件：**
- 新建： `src/quant/strategies/combo.py`
- 修改： `src/quant/strategies/__init__.py`（注册）
- 测试： `tests/test_combo.py`

**接口：**
- 依赖： `MomentumSelect`、`MomentumParams`（任务 4）、`SwingPullback`、`SwingParams.use_inverse`（任务 3）
- 提供：
  - `ComboParams`：`momentum_weight=0.4, swing_weight=0.6, vol_symbol="QQQ", vol_window=20, vol_threshold=0.30, vol_scale=0.5, momentum: Mapping = {}, swing: Mapping = V2_SWING`；`V2_SWING = {"trend_ma": 120, "slope_days": 10, "support_ma": 20, "confirm": "close_strength", "exit_ma": 10, "use_inverse": False}`，用户传入的 `swing` 覆盖其中同名键。`momentum_weight + swing_weight` 必须 ≤ 1，否则 `ValueError`。`ComboParams.from_mapping(params)` 拒绝未知参数；`ComboParams.swing_params() -> dict` 返回合并后的 swing 参数，`momentum_params() -> dict` 返回 momentum 参数。
  - `vol_factor(close: pd.Series, window: int, threshold: float, scale: float) -> float`：最近 `window` 个日收益率的标准差 × √252 > threshold 时返回 scale，否则 1.0；有效收益率不足 `window` 个时返回 1.0。
  - `merge_weights(momentum: Mapping[str, float], swing: Mapping[str, float], mw: float, sw: float, factor: float) -> dict[str, float]`：`(mw × m[s] + sw × w[s]) × factor`，同一标的相加。
  - `Combo(Strategy)`：`name = "combo"`，`min_weight_diff = 0.04`，`max_daily_move = 0.6`。`target_weights` 用 `dataclasses.replace(ctx, params=...)` 分别调用两个子策略。`momentum_weight == 0` 或 `swing_weight == 0` 时跳过对应子策略（用于单独回测各部分）。`symbols` = 子策略 swing 的 symbols ∪ {vol_symbol}；`warmup_days` = 两者最大值。

  `min_weight_diff = 0.04` 的理由：择时把动量仓从 10% 减到 5%、V2 仓从 15% 减到 7.5%，阈值必须小于 0.05 才能执行减仓；同时保持足够大，避免价格漂移触发来回调仓（动量仓需涨约 40% 才触发）。

- [ ] **步骤 1： 写失败的测试**

```python
def test_vol_factor() -> None:
    calm = pd.Series(100 * np.cumprod(np.full(30, 1.001)))
    wild = pd.Series(100 * np.cumprod(np.tile([1.03, 0.97], 15)))  # 年化约 48%
    assert vol_factor(calm, 20, 0.30, 0.5) == 1.0
    assert vol_factor(wild, 20, 0.30, 0.5) == 0.5
    assert vol_factor(wild.iloc[:10], 20, 0.30, 0.5) == 1.0   # 数据不足
    assert vol_factor(pd.Series([np.nan] * 30), 20, 0.30, 0.5) == 1.0

def test_merge_weights_adds_overlap() -> None:
    out = merge_weights({"MU": 0.25, "WDC": 0.25}, {"MU": 0.25, "AAPL": 0.25}, 0.4, 0.6, 1.0)
    assert out == pytest.approx({"MU": 0.25, "WDC": 0.10, "AAPL": 0.15})
    assert merge_weights({"WDC": 0.25}, {"AAPL": 0.25}, 0.4, 0.6, 0.5) == pytest.approx({"WDC": 0.05, "AAPL": 0.075})

def test_params_validation() -> None:
    with pytest.raises(ValueError):
        ComboParams.from_mapping({"momentum_weight": 0.5, "swing_weight": 0.6})

def test_swing_defaults_are_v2() -> None:
    assert SwingParams.from_mapping(ComboParams().swing_params()).slope_days == 10
    assert "PSQ" not in Combo().symbols({})

def test_combo_end_to_end() -> None:
    # 合成数据：swing 的股票池用 2 只、动量成分股 3 只、QQQ；跑 Backtester 60 个交易日
    # 断言：每天的目标权重和 ≤ 1；只有 momentum 时（swing_weight=0）结果与单独 MomentumSelect × 0.4 一致
```

- [ ] **步骤 2： 运行，确认失败**

运行：`TRADING_MODE=sim uv run pytest tests/test_combo.py -v`
预期：测试失败（模块不存在）

- [ ] **步骤 3： 实现**

- [ ] **步骤 4： 运行通过；`make check` 通过**

- [ ] **步骤 5： 提交** `feat: combo 组合策略（动量 + V2 + 波动率择时）（Refs #47）`

---

### 任务 6： 通过标准判定

**文件：**
- 修改： `src/quant/engine/evaluation.py`
- 测试： `tests/test_trades_evaluation.py`

**接口：**
- 提供： `combo_checks(full: Metrics, qqq_full: Metrics, ins: Metrics, qqq_ins: Metrics, oos: Metrics, qqq_oos: Metrics, neighbors: Sequence[Metrics]) -> dict[str, bool]`，键与含义（边界用现有 `_ge` 容差）：
  - `"最大回撤 ≥ -25%"`：`full.max_drawdown ≥ -0.25`
  - `"年化 ≥ QQQ + 3 个百分点"`：`full.cagr ≥ qqq_full.cagr + 0.03`
  - `"夏普 ≥ 1.1"`：`full.sharpe ≥ 1.1`
  - `"两个区间夏普都高于 QQQ"`：`ins.sharpe > qqq_ins.sharpe and oos.sharpe > qqq_oos.sharpe`
  - `"相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0"`：neighbors 全部满足（空列表视为不满足）

- [ ] **步骤 1： 写失败的测试**：边界值恰好等于门槛时为 True（如 `max_drawdown=-0.25`、`cagr = qqq + 0.03`）；任一相邻设置回撤 -0.31 时最后一项为 False；neighbors 为空时为 False。

- [ ] **步骤 2： 运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_trades_evaluation.py -v`

- [ ] **步骤 3： 实现**

- [ ] **步骤 4： 运行通过**

- [ ] **步骤 5： 提交** `feat: combo 通过标准判定（Refs #47）`

---

### 任务 7： 研究脚本与报告

**文件：**
- 新建： `scripts/research/combo.py`、`docs/research/2026-10-30-combo.md`（由脚本生成）
- 测试： `tests/test_research_combo.py`

**接口：**
- 依赖： 任务 1–6 的全部接口；`ensure_bars`、`ParquetStore`、`YFinanceProvider`、`compute_metrics`、`round_trips`、`trade_stats`、`load_earnings`、`save_earnings`、`load_fomc_dates`；`scripts/research/swing_pullback.py` 的进程池写法（`_init` 把 bars / events / universe 放进子进程全局）。
- 提供（脚本内的纯函数，供测试）：
  - `CONFIGS: dict[str, dict]`，键与参数严格为：
    - `"组合（持有过财报）"`: `{}`
    - `"组合（财报前离场）"`: `{"momentum": {"earnings_exit": True}}`
    - `"相邻：比例 30/70"`: `{"momentum_weight": 0.3, "swing_weight": 0.7}`
    - `"相邻：比例 50/50"`: `{"momentum_weight": 0.5, "swing_weight": 0.5}`
    - `"相邻：阈值 25%"`: `{"vol_threshold": 0.25}`
    - `"相邻：阈值 35%"`: `{"vol_threshold": 0.35}`
    - `"相邻：动量 3 只"`: `{"momentum": {"top_n": 3}}`
    - `"相邻：动量 5 只"`: `{"momentum": {"top_n": 5}}`
    - `"单独：动量仓"`: `{"momentum_weight": 1.0, "swing_weight": 0.0, "vol_scale": 1.0}`
    - `"单独：V2 仓"`: `{"momentum_weight": 0.0, "swing_weight": 1.0, "vol_scale": 1.0}`
  - `missing_ratio_by_year(universe: Constituents, have: Collection[str], start: date, end: date) -> pd.Series`：每年各快照中“没有数据的成员”占比的平均值。
  - `split_metrics(equity: pd.Series, oos_start: date) -> tuple[Metrics, Metrics, Metrics]`：全区间、调参区间（< oos_start）、样本外（≥ oos_start 前一交易日起，保证连续）。

脚本流程（`main`）：
1. 参数：`--constituents PATH`（缺省时从 `https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv` 下载到 `data_dir/universe/sp500_history.csv`）、`--user-agent`（必填，SEC 用）、`--workers`（默认 6）。
2. 数据：`all_symbols(2014-06-01, 结束日)` ∪ V2 标的 ∪ {QQQ, SPY}；逐只 `ensure_bars(..., max_move=0.6)`，捕获 `DataUnavailableError` 记入“缺失”、`DataValidationError` 记入“校验未通过”（两者都排除，报告中列出）。
3. 财报：先对每个月末用 `rank_momentum` 取前 5（覆盖相邻设置），得到曾入选集合；用 `fetch_cik_map` + `fetch_earnings(ciks=...)` 拉取，缓存到 `data_dir/events/earnings_sp500.csv`（已存在则复用，加 `--refresh-earnings` 重新拉取）；与 `earnings.csv` 合并成一个 `EventCalendar`。无法映射 CIK 的代码数写入报告。
4. 回测：`CONFIGS` 每组一次 `Backtester(Combo(), bars, ..., universe=..., events=...)`，区间 2015-01-02 至最近已收盘交易日，进程池并行。
5. 判定：`combo_checks` 用“组合（持有过财报）”与 6 组相邻设置；“财报前离场”只并列报告，交给用户选择。
6. 报告：结论（通过 / 未通过及逐项）；全部 10 组的全区间、调参、样本外指标（年化、最大回撤、夏普、卡玛、波动、换手）；QQQ / SPY 基准；两种财报处理的对比与逐年收益；交易统计；数据缺失逐年比例、校验未通过列表、成分名单版本日期；已知局限（幸存者偏差、样本外已看过、新浪口径差异留待第二份计划）。

- [ ] **步骤 1： 写失败的测试**

```python
def test_configs() -> None:
    assert len(CONFIGS) == 10
    assert CONFIGS["组合（持有过财报）"] == {}
    for name, cfg in CONFIGS.items():
        ComboParams.from_mapping(cfg)  # 全部参数合法

def test_missing_ratio_by_year() -> None:
    u = parse_sp500_history('date,tickers\n2015-01-02,"A,B,C,D"\n2016-01-04,"A,B"\n')
    r = missing_ratio_by_year(u, {"A", "B"}, date(2015, 1, 2), date(2016, 12, 30))
    assert r[2015] == pytest.approx(0.5) and r[2016] == pytest.approx(0.0)

def test_split_metrics_continuous() -> None:
    # 合成净值：oos 段第一天收益来自前一交易日收盘，不丢失一天
```

- [ ] **步骤 2： 运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_research_combo.py -v`

- [ ] **步骤 3： 实现脚本**

- [ ] **步骤 4： 测试通过；`make check` 通过**

- [ ] **步骤 5： 在 Mac 上运行研究**（较慢，后台运行；数据下载约 20–40 分钟，SEC 财报约 30–40 分钟，回测约 10–20 分钟）

运行：`NO_PROXY=data.sec.gov,www.sec.gov no_proxy=data.sec.gov,www.sec.gov TRADING_MODE=sim uv run python scripts/research/combo.py --user-agent "<用户同意使用的邮箱>"`
预期： 生成 `docs/research/2026-10-30-combo.md`，含 10 组结果与判定。结果与快速试验（附录）差异较大时，先查原因（`min_weight_diff`、择时执行时点、成本），在报告中说明，不调参数去对齐。

- [ ] **步骤 6： 提交** `docs: combo 研究报告（Refs #47）`，并在 #47 评论结论与两种财报处理的对比。
