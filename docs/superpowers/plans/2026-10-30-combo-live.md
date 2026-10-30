# 组合策略 combo 接入模拟盘 实施计划

> **给执行者：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐个实施本计划。步骤用复选框（`- [ ]`）跟踪进度。

**目标：** 让服务器的每日任务能运行 `combo`：动量仓用新浪日线 + 老虎拆股记录排序，V2 仓用老虎复权日线 + 老虎财报日历，风控白名单随标普 500 成分股变化；最后准备好模拟盘切换配置（合入 main 前需用户确认）。

**架构：** 数据层新增三件事——成分名单快照同步、候选池日线同步（新浪 + 拆股调整，存到单独的 Parquet 目录）、实盘财报日同步（老虎财报日历）。`daily_data` 按策略声明的需要调用它们；`daily_signal` 给策略提供完整的 Context（`field_loader` 先查主数据目录再查候选池目录、`events`、`universe`）。风控白名单 = 配置的固定列表 ∪ 近 62 天内的成分股。

**技术栈：** Python 3.12、pandas、tigeropen、uv、pytest、ruff、mypy strict。

**设计文档：** `docs/superpowers/specs/2026-10-30-momentum-combo-design.md`（第 5.2、6、8 节；前一份计划 `docs/superpowers/plans/2026-10-30-momentum-combo.md` 已完成）

## 全局约束

- 策略只输出目标权重，纯计算、无 I/O；回测与实盘同一个类。数据同步只在 `daily_data` / CLI 中做。
- 下单相关代码必须检查 `TRADING_MODE`；非模拟账户拒绝执行。白名单变化涉及风控，相关提交加 `safety` 说明。
- 老虎历史日线额度 30 天 20 个 symbol：老虎日线只用于固定标的（七姐妹 + MU + QQQ + 基准 SPY，以及切换期的 TLT、GLD、SHY）；候选池（约 500 只）一律用新浪。延迟报价、拆股记录、财报日历不占额度（2026-10-30 实测）。
- 新浪日 K 不做拆股和分红调整：除权日之前的开高低收乘以 `from_factor / to_factor`、成交量除以同一比例；`adj_close` 记为调整后的收盘价（不含分红）。
- 老虎 `get_corporate_split` 每次最多 20 个代码。
- 实盘财报反应日：`report_time` 为“盘后”→ 下一交易日；其余 → `report_date` 当日。
- 动量仓持有过财报（`earnings_exit=False`）。
- 不在服务器上改代码；配置改动随 CI 部署。**改 `configs/paper.yaml` 的策略为 `combo` 的那次 test → main 合并，必须先得到用户确认。**
- 不打印 `.env`、老虎配置、webhook key；SEC / 老虎账户信息不写入日志。
- 测试统一 `TRADING_MODE=sim`，不访问网络（用假客户端）。

## 评审重点（测试之外最容易出问题的输入）

1. **新浪个别代码拿不到数据**：覆盖率 ≥ 95% 时继续并在通知中列出缺失代码；低于 95% 时任务失败告警——任务 3 测试。
2. **拆股除权日当天**：只调整除权日之前的数据，除权日当天及之后不变——任务 1 测试。
3. **持有的动量股在月中被调出标普 500**：仍在白名单内（近 62 天的成分股并集），不会因“持仓含白名单外标的”停机——任务 6 测试。
4. **月中首次部署**：本月调仓日（上月末）的候选池数据不在本地时，应补拉到调仓日，而不是空仓——任务 5 测试。
5. **财报时间为“盘后”或未知**：盘后 → 下一交易日；未知 → 当日——任务 1 测试。

---

### 任务 1：老虎拆股记录、财报日历与拆股调整

**文件：**
- 修改：`src/quant/data/providers/tiger.py`
- 新建：`src/quant/data/splits.py`
- 测试：`tests/test_tiger_provider.py`、`tests/test_splits.py`

**接口：**
- 提供：
  - `TigerProvider.splits(symbols: Sequence[str], start: date, end: date) -> dict[str, list[tuple[date, float]]]`：按 20 个一批调用 `get_corporate_split(symbols=..., market="US", begin_date=..., end_date=...)`，每批前 `_throttle()`；返回 `{代码: [(除权日, 价格乘数 from_factor / to_factor)]}`，按日期排序；没有记录的代码不出现在结果中。接口报错抛 `DataUnavailableError`。
  - `TigerProvider.earnings_calendar(symbols: Collection[str], start: date, end: date, cal: TradingCalendar) -> dict[str, list[date]]`：调用 `get_corporate_earnings_calendar(market="US", begin_date, end_date)`（区间超过 30 天时按 30 天分段），只保留 `symbols` 中的代码，按上面的规则换算成反应日。
  - `quant.data.splits.split_adjust(bars: pd.DataFrame, splits: Sequence[tuple[date, float]]) -> pd.DataFrame`：纯函数；对每个除权日 d，`index < d` 的 open/high/low/close 乘以乘数、volume 除以乘数（取整为 int64）；返回副本，并令 `adj_close = close`。

- [ ] **步骤 1：写失败的测试**

```python
def test_split_adjust_before_ex_date_only() -> None:
    # 2024-06-07 收盘 1208.88、06-10 121.79；NVDA 10 拆 1（乘数 0.1）
    bars = <两行：06-07 close 1208.88 volume 41238499；06-10 close 121.79 volume 314162647>
    out = split_adjust(bars, [(date(2024, 6, 10), 0.1)])
    assert out.loc["2024-06-07", "close"] == pytest.approx(120.888)
    assert out.loc["2024-06-07", "volume"] == 412384990
    assert out.loc["2024-06-10", "close"] == pytest.approx(121.79)  # 除权日不变
    assert (out["adj_close"] == out["close"]).all()

def test_split_adjust_multiple_splits_compound() -> None:
    # 两次 2 拆 1：最早的数据乘以 0.25

def test_splits_batches_of_20() -> None:
    # 假客户端记录每次调用的 symbols；45 个代码 → 3 次调用（20、20、5）；返回 from/to 换算后的乘数

def test_earnings_calendar_reaction_day() -> None:
    # report_time "盘后" 2026-10-28（周三）→ 10-29；"盘前" 10-28 → 10-28；空值 → 当日；
    # 不在 symbols 中的代码被过滤；区间 70 天 → 分 3 段调用
```

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_splits.py tests/test_tiger_provider.py -v`（预期：新增用例失败）

- [ ] **步骤 3：实现**

- [ ] **步骤 4：运行通过**

- [ ] **步骤 5：提交** `feat: 老虎拆股记录、财报日历与拆股调整（Refs #47）`

---

### 任务 2：成分名单快照同步

**文件：**
- 修改：`src/quant/data/universe.py`、`scripts/research/combo.py`（改用新函数，删除脚本内的 `fetch_constituents`）
- 测试：`tests/test_universe.py`

**接口：**
- 提供：
  - `SP500_URL: str`（即研究脚本中的 `CONSTITUENTS_URL`，移到这里）
  - `constituents_path(data_dir: Path) -> Path`：`data_dir / "universe" / "sp500_history.csv"`
  - `sync_constituents(path: Path, today: date, fetch: Callable[[str], bytes] | None = None, max_age_days: int = 7) -> Constituents`：文件不存在或修改时间早于 `max_age_days` 天时下载（先写临时文件、解析成功后再替换，避免写坏）；下载失败但旧文件存在时沿用旧文件并记 warning；都没有时抛 `DataUnavailableError`。
  - `stale_warning(u: Constituents, today: date, days: int = 120) -> str | None`：最后一条变动距今超过 `days` 天时返回提示文字（成分变动通常每季度都有，长期没有更新说明数据集可能停更）。

- [ ] **步骤 1：写失败的测试**：
  - 文件不存在 → 调用 fetch 一次并写入；
  - 文件新 → 不调用 fetch；
  - fetch 抛错且旧文件存在 → 返回旧数据；
  - fetch 返回无法解析的内容 → 原文件保持不变并抛错；
  - `stale_warning` 在 121 天时返回文字、119 天时返回 None。

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_universe.py -v`

- [ ] **步骤 3：实现**

- [ ] **步骤 4：运行通过；`tests/test_research_combo.py` 仍通过**

- [ ] **步骤 5：提交** `feat: 标普 500 成分名单快照同步（Refs #47）`

---

### 任务 3：候选池日线同步（新浪 + 拆股调整）

**文件：**
- 新建：`src/quant/data/universe_bars.py`
- 测试：`tests/test_universe_bars.py`

**接口：**
- 依赖：`split_adjust`、`TigerProvider.splits`（任务 1）
- 提供：
  - `universe_store(data_dir: Path) -> ParquetStore`：`ParquetStore(data_dir / "universe_bars")`
  - `SplitSource = Callable[[Sequence[str], date, date], dict[str, list[tuple[date, float]]]]`
  - `@dataclass UniverseSync: fetched: list[str]; skipped: list[str]; missing: list[str]; excluded: dict[str, str]`，`coverage(n_members: int) -> float`
  - `sync_universe_bars(store: ParquetStore, provider: DataProvider, splits: SplitSource | None, cal: TradingCalendar, members: Collection[str], start: date, end: date, max_move: float = 0.4, min_coverage: float = 0.95) -> UniverseSync`：
    - 本地数据最后一天 ≥ end 的代码跳过（`skipped`）；其余用 provider 拉 [start, end]，`splits` 不为 None 时对整批代码查一次拆股并调用 `split_adjust`，再 `store.write_bars(s, df, provider.source)`；
    - 拉取失败记入 `missing`；拆股调整后 `validate_bars(..., max_move=0.4)` 不通过的记入 `excluded`（删除其本地数据，不参与排序；没识别出的拆股会表现为 -50% 以上的单日跌幅）；
    - 覆盖率 `(成员数 - missing - excluded) / 成员数` 低于 `min_coverage` 时抛 `DataValidationError`。

- [ ] **步骤 1：写失败的测试**（假 provider + 假 splits + 临时目录）：
  - 100 个成员、3 个拉取失败 → 完成，`missing` 为这 3 个、覆盖率 0.97；
  - 6 个失败 → 抛错（覆盖率 0.94）；
  - 有拆股记录的代码写入的是调整后的价格；
  - 未记录的 2 拆 1（价格腰斩）→ 进入 `excluded`，本地无该代码数据；
  - 已有最新数据的代码不再拉取。

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_universe_bars.py -v`

- [ ] **步骤 3：实现**

- [ ] **步骤 4：运行通过**

- [ ] **步骤 5：提交** `feat: 候选池日线同步（新浪 + 拆股调整）（Refs #47）`

---

### 任务 4：实盘财报日

**文件：**
- 修改：`src/quant/data/events.py`
- 测试：`tests/test_events_data.py`

**接口：**
- 依赖：`TigerProvider.earnings_calendar`（任务 1）
- 提供：
  - `live_earnings_path(data_dir: Path) -> Path`：`data_dir / "events" / "earnings_live.csv"`
  - `sync_live_earnings(path: Path, fetch: Callable[[date, date], Mapping[str, Sequence[date]]], today: date, back_days: int = 400, ahead_days: int = 45) -> dict[str, tuple[date, ...]]`：拉取 [today - back_days, today + ahead_days]，与已有文件按代码合并去重（已有的历史日期保留，避免接口只返回近期数据时丢失），保存并返回。
  - `load_events(data_dir: Path) -> EventCalendar`：合并 `earnings_live.csv` 与 `earnings.csv`（存在时）+ `load_fomc_dates()`。

- [ ] **步骤 1：写失败的测试**：
  - 第一次同步写入；
  - 第二次 fetch 只返回新日期时，旧日期仍在；
  - `load_events` 合并两个文件、文件不存在时不报错、含 FOMC。

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_events_data.py -v`

- [ ] **步骤 3：实现**

- [ ] **步骤 4：运行通过**

- [ ] **步骤 5：提交** `feat: 实盘财报日同步（老虎财报日历）（Refs #47）`

---

### 任务 5：每日任务接入（daily_data / daily_signal）

**文件：**
- 修改：`src/quant/core/settings.py`（`DataSettings.universe`）、`src/quant/strategies/base.py`（`Strategy.uses_universe`、`Strategy.earnings_symbols`）、`src/quant/strategies/{swing_pullback,momentum_select,combo}.py`、`src/quant/engine/backtester.py`（`store_field_loader`）、`src/quant/jobs/daily.py`
- 测试：`tests/test_jobs.py`、`tests/test_settings.py`

**接口：**
- 依赖：任务 1–4 的全部函数；`rebalance_day`（momentum_select）
- 提供：
  - `DataSettings.universe: Literal["none", "sina", "yfinance"] = "none"`；为 `"sina"` 时 `primary` 必须是 `"tiger"`（拆股记录来自老虎），否则配置校验报错。
  - `Strategy.uses_universe: ClassVar[bool] = False`（`MomentumSelect`、`Combo` 为 True）；`Strategy.earnings_symbols(params) -> list[str]` 默认 `[]`（`SwingPullback` 返回 `stocks`；`Combo` 返回 swing 部分的 `stocks`；`swing_weight == 0` 时为空）。
  - `store_field_loader(stores: Sequence[ParquetStore]) -> FieldLoader`：每个代码取第一个有数据的 store，按 `adjusted_ohlc` 输出含分红复权口径；放在 `engine/backtester.py` 的 `field_loader_from_bars` 旁边。
  - `JobContext` 新增 `universe_provider: DataProvider | None`（`settings.data.universe` 为 sina / yfinance 时创建），`from_settings` 负责创建。
  - `daily_data` 在现有流程之后：
    1. 策略 `uses_universe` 时：`sync_constituents` → `me = rebalance_day(calendar, d)` → `sync_universe_bars(universe_store, universe_provider, provider.splits（仅 universe == "sina"）, calendar, universe.members(me), me - 450 天, me)`；返回文字中加入覆盖率，有 missing / excluded 或 `stale_warning` 时发 WARNING 通知；
    2. `earnings_symbols` 非空且 primary 为 tiger 时：`sync_live_earnings`；
    3. `uses_universe` 但 `universe_provider` 为 None 时抛错（配置缺失，不静默跳过）。
  - `daily_signal` 构造 `Context(as_of=d, loader=store.prices, params=..., field_loader=store_field_loader([store, universe_store]), events=load_events(data_dir), universe=load_constituents(...) if uses_universe else None)`。

- [ ] **步骤 1：写失败的测试**（沿用 `tests/test_jobs.py` 的假券商、假通知、临时目录；假 provider 有 `splits` 与 `earnings_calendar`）：
  - `etf_momentum` 的 daily_data 行为不变（不同步候选池、不访问财报日历）；
  - combo 在 2024-06-10 运行 daily_data：候选池同步到 2024-05-31（上月末，月中首次部署的情形），不是 06-10；
  - combo 在月末 2024-06-28 运行：同步到 06-28；
  - 覆盖率不足时 daily_data 失败；
  - combo 的 daily_signal 写入的目标权重包含动量股（来自候选池目录）与 V2 股（来自主目录）；
  - `data.universe = "sina"` 且 `primary = "yfinance"` 时配置报错。

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_jobs.py tests/test_settings.py -v`

- [ ] **步骤 3：实现**

- [ ] **步骤 4：运行通过；`make check` 通过**

- [ ] **步骤 5：提交** `feat: 每日任务支持组合策略的数据与 Context（Refs #47）`

---

### 任务 6：动态白名单（safety）

**文件：**
- 修改：`src/quant/core/settings.py`（`RiskSettings.universe_whitelist`）、`src/quant/risk/rules.py`、`src/quant/engine/executor.py`
- 测试：`tests/test_risk.py`、`tests/test_executor.py`

**接口：**
- 提供：
  - `RiskSettings.universe_whitelist: Literal["none", "sp500"] = "none"`
  - `RiskLimits.from_settings(settings, extra_symbols: Collection[str] = ()) -> RiskLimits`：白名单 = `symbol_whitelist` ∪ `extra_symbols`；固定白名单为空的检查不变。
  - `whitelist_extra(settings: Settings, d: date) -> frozenset[str]`（`risk/rules.py`）：`universe_whitelist == "sp500"` 时返回 `Constituents.all_symbols(d - 62 天, d)`；名单文件不存在时抛 `ConfigError`（拒绝执行，不退化为固定白名单之外全部拒绝的含糊状态）；`"none"` 时返回空集。
  - `Executor.run(trade_date)` 用 `whitelist_extra(settings, trade_date)` 构造本次的 `RiskLimits`。

- [ ] **步骤 1：写失败的测试**：
  - `universe_whitelist="none"` 时目标含 `WDC` → whitelist 违规（行为不变）；
  - `"sp500"` 且 `WDC` 为当前成分股 → 通过；
  - 持仓含 40 天前被调出的代码 → `check_holdings` 不报违规；含 70 天前被调出的 → 报违规；
  - `"sp500"` 但名单文件不存在 → `ConfigError`；
  - executor 端到端：combo 目标含成分股时下单成功（沿用现有假券商）。

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_risk.py tests/test_executor.py -v`

- [ ] **步骤 3：实现**

- [ ] **步骤 4：运行通过；`make check` 通过**

- [ ] **步骤 5：提交** `feat: 风控白名单支持标普 500 成分股（safety，Refs #47）`

---

### 任务 7：信号预览命令、服务器验证与切换配置

**文件：**
- 修改：`src/quant/cli.py`、`configs/paper.yaml`、`CLAUDE.md`（常用命令）
- 测试：`tests/test_cli.py`

**接口：**
- 提供：`quant preview --strategy NAME --date D [--sync]`：用当前配置的数据源，`--sync` 时先执行与 `daily_data` 相同的数据同步（主数据、候选池、财报日；不写 `job_runs`、不写 `signals`、不发通知），再按 `daily_signal` 的方式构造 Context 运行策略，打印目标权重、择时系数、动量排名前 10 与候选池覆盖率。不连接券商、不下单。

- [ ] **步骤 1：写失败的测试**：用假数据源运行 `preview --strategy combo --date 2024-06-10 --sync`，输出含“目标仓位”“覆盖率”；数据库中 `signals`、`job_runs` 无新增行。

- [ ] **步骤 2：运行，确认失败** `TRADING_MODE=sim uv run pytest tests/test_cli.py -v -k preview`

- [ ] **步骤 3：实现；`CLAUDE.md` 常用命令加一行**

- [ ] **步骤 4：运行通过；`make check` 通过；提交** `feat: quant preview 信号预览命令（Refs #47）`

- [ ] **步骤 5：合入 test → 发 PR 合入 main → 部署**（这一步只部署代码，`paper.yaml` 仍是 `etf_momentum`，模拟盘行为不变）。PR 描述列出 #46、#47（Refs，不关闭 #47）、#6、#7（Closes）。**合入 main 前告知用户并等待确认**（会触发部署）。

- [ ] **步骤 6：服务器验证**（部署后）：`docker compose exec scheduler quant preview --strategy combo --date <最近交易日> --sync`。检查并记录到 #47：
  - 候选池覆盖率 ≥ 95%，缺失 / 排除的代码；
  - 动量前 4 与 Mac 回测在同一调仓日的前 4 是否一致（不一致时说明原因：新浪不含分红、成分名单差异等）；
  - 老虎额度（`kline_quota`）：已用不超过 14 个；
  - 运行耗时。

- [ ] **步骤 7：准备切换配置**（新分支 `feat/47-switch-paper-combo`）：`configs/paper.yaml`：
  - `strategy.name: combo`；
  - `data.universe: sina`；
  - `risk.symbol_whitelist: [AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, MU, QQQ, SPY, TLT, GLD, SHY]`（保留 ETF 以便切换日卖出旧持仓）；
  - `risk.universe_whitelist: sp500`；
  - `risk.max_symbol_weight: 0.3`。

  合入 test。**合入 main（即替换模拟盘策略）必须由用户明确确认**；确认后发 PR，切换当日人工复核全部订单。
