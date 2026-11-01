"""服务器回测数据（#66）：backtest-worker 空闲时按 Tiingo 额度逐只补齐，回测只读本地数据。

优先级：固定标的（各策略标的、SPY/QQQ、面板 ETF 选项）→ 当前标普 500 成分 → 已退市的历史成分。
每次拉 2015-01-01 至今的全量（复权价始终自洽）；仍在市的超过 30 天重新拉，已退市的只拉一次。
本月拿不到 / 校验不过的代码，本月不再请求（避免反复消耗额度），下个月再试。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from quant.core.calendar import TradingCalendar
from quant.core.settings import Settings
from quant.data.events import load_earnings, save_earnings
from quant.data.providers.base import DataProvider, DataUnavailableError
from quant.data.quota import QuotaExceeded, QuotaLedger, QuotaUsage, atomic_write_text
from quant.data.store import ParquetStore
from quant.data.validate import validate_bars
from quant.strategies import ETF_OPTIONS, STRATEGIES
from quant.strategies.combo import Combo
from quant.strategies.universe import Constituents

log = logging.getLogger("quant.backtest_data")

FULL_START = date(2015, 1, 1)
HISTORY_START = date(2016, 1, 1)  # 历史成分从这一年起计入（回测通常从 2016 年开始）
REFRESH_DAYS = 30
EARNINGS_CHUNK = 50  # 每次空闲同步多少只的财报日（SEC 每秒 10 次以内）
BENCHMARKS = ("SPY", "QQQ")

Outcome = Literal["fetched", "unavailable", "invalid", "quota", "idle"]


def backtest_store(data_dir: Path) -> ParquetStore:
    return ParquetStore(data_dir / "backtest_bars")


def state_path(data_dir: Path) -> Path:
    return data_dir / "state" / "backtest_data.json"


def quota_path(data_dir: Path) -> Path:
    return data_dir / "state" / "tiingo_quota.json"


def earnings_path(data_dir: Path) -> Path:
    return data_dir / "events" / "earnings_sp500.csv"


@dataclass(frozen=True)
class Wanted:
    fixed: list[str]
    current: list[str]
    delisted: list[str]

    @property
    def universe(self) -> list[str]:
        return [*self.current, *self.delisted]


def fixed_symbols() -> list[str]:
    out = set(BENCHMARKS) | set(ETF_OPTIONS)
    for cls in STRATEGIES.values():
        try:
            out |= set(cls().symbols({}))
        except NotImplementedError:
            continue
    return sorted(out)


def wanted_symbols(universe: Constituents | None, today: date) -> Wanted:
    fixed = fixed_symbols()
    if universe is None:
        return Wanted(fixed, [], [])
    now = universe.members(today)
    ever = universe.all_symbols(HISTORY_START, today)
    return Wanted(fixed, sorted(now - set(fixed)), sorted(ever - now - set(fixed)))


@dataclass
class SymbolState:
    status: Literal["ok", "unavailable", "invalid"]
    last_fetch: str  # 最近一次请求的日期（美东）
    month: str  # 最近一次请求所在月份
    last_date: str | None = None  # 本地数据的最新交易日
    reason: str = ""


@dataclass
class BackfillState:
    path: Path
    symbols: dict[str, SymbolState] = field(default_factory=dict)
    earnings_month: str | None = None  # 最近一次完成财报日同步的月份
    earnings_done: list[str] = field(default_factory=list)  # 进行中月份已同步的代码
    earnings_progress: str | None = None  # earnings_done 所属的月份

    @classmethod
    def load(cls, path: Path) -> BackfillState:
        if not path.exists():
            return cls(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path,
            {k: SymbolState(**v) for k, v in raw.get("symbols", {}).items()},
            raw.get("earnings_month"),
            raw.get("earnings_done", []),
            raw.get("earnings_progress"),
        )

    def save(self) -> None:
        doc = {
            "symbols": {k: asdict(v) for k, v in sorted(self.symbols.items())},
            "earnings_month": self.earnings_month,
            "earnings_done": self.earnings_done,
            "earnings_progress": self.earnings_progress,
        }
        atomic_write_text(self.path, json.dumps(doc, ensure_ascii=False, indent=1))


def _needs(st: SymbolState | None, active: bool, today: date, month: str) -> bool:
    if st is None:
        return True
    if st.status != "ok":
        return st.month != month  # 本月失败过的不再试
    return active and date.fromisoformat(st.last_fetch) <= today - timedelta(days=REFRESH_DAYS)


def next_symbol(
    wanted: Wanted, state: BackfillState, ledger: QuotaLedger, today: date
) -> str | None:
    month = ledger.usage().month
    left = ledger.month_left()
    groups: tuple[tuple[Sequence[str], bool], ...] = (
        (wanted.fixed, True), (wanted.current, True), (wanted.delisted, False),
    )  # fmt: skip
    for symbols, active in groups:
        for s in symbols:
            if not _needs(state.symbols.get(s), active, today, month):
                continue
            if left <= 0 and not ledger.used(s):
                continue
            return s
    return None


@dataclass(frozen=True)
class StepResult:
    symbol: str | None
    outcome: Outcome


def backfill_step(
    provider: DataProvider,
    store: ParquetStore,
    state: BackfillState,
    ledger: QuotaLedger,
    wanted: Wanted,
    today: date,
    cal: TradingCalendar,
) -> StepResult:
    """补一只；额度用尽时不改状态，返回 quota。"""
    s = next_symbol(wanted, state, ledger, today)
    if s is None:
        return StepResult(None, "idle")
    month = ledger.usage().month
    try:
        df = provider.daily_bars(s, FULL_START, today)
    except QuotaExceeded:
        return StepResult(s, "quota")
    except DataUnavailableError as e:
        state.symbols[s] = SymbolState("unavailable", today.isoformat(), month, reason=str(e))
        state.save()
        return StepResult(s, "unavailable")
    first, last = df.index[0].date(), df.index[-1].date()
    report = validate_bars(s, df, cal, first, last, max_move=Combo.max_daily_move)
    if not report.ok:
        state.symbols[s] = SymbolState("invalid", today.isoformat(), month,
                                       reason=report.summary(limit=3))  # fmt: skip
        state.save()
        return StepResult(s, "invalid")
    store.write_bars(s, df, provider.source)
    state.symbols[s] = SymbolState("ok", today.isoformat(), month, last_date=last.isoformat())
    state.save()
    return StepResult(s, "fetched")


EarningsFetch = Callable[[str, str], list[date]]


def _default_earnings_fetch(symbol: str, user_agent: str) -> list[date]:
    from quant.data.providers import edgar

    return edgar.fetch_earnings(symbol, user_agent)


def sync_earnings_step(
    state: BackfillState,
    data_dir: Path,
    user_agent: str | None,
    wanted: Wanted,
    today: date,
    fetch: EarningsFetch = _default_earnings_fetch,
    limit: int = EARNINGS_CHUNK,
) -> int:
    """固定标的与当前成分都补齐后，每月一次从 SEC EDGAR 同步候选池股票的历史财报反应日。

    返回本次处理的代码数；未配置联系方式、日线未补齐或本月已完成时为 0。单只失败不中断。
    """
    month = today.strftime("%Y-%m")
    if not user_agent or state.earnings_month == month:
        return 0
    if any(state.symbols.get(s) is None or state.symbols[s].status != "ok"
           for s in [*wanted.fixed, *wanted.current]):  # fmt: skip
        return 0
    if state.earnings_progress != month:
        state.earnings_progress, state.earnings_done = month, []
    done = set(state.earnings_done)
    todo = [s for s in wanted.universe if s not in done][:limit]
    path = earnings_path(data_dir)
    data = {k: list(v) for k, v in load_earnings(path).items()} if path.exists() else {}
    for s in todo:
        try:
            got = fetch(s, user_agent)
        except Exception as e:  # 单只失败（无 CIK、网络等）不影响其他
            log.warning("财报日 %s 同步失败：%s", s, type(e).__name__)
            got = []
        if got:
            data[s] = sorted(set(got))
        done.add(s)
    save_earnings(path, data)
    state.earnings_done = sorted(done)
    if all(s in done for s in wanted.universe):
        state.earnings_month = month
    state.save()
    return len(todo)


@dataclass(frozen=True)
class BackfillStatus:
    fixed_done: int
    fixed_total: int
    current_done: int
    current_total: int
    delisted_done: int
    delisted_total: int
    data_end: date | None
    unavailable: list[str]
    quota: QuotaUsage
    earnings: Literal["complete", "partial", "none"]


def status(
    wanted: Wanted, state: BackfillState, ledger: QuotaLedger, data_dir: Path | None
) -> BackfillStatus:
    def ok(s: str) -> bool:
        st = state.symbols.get(s)
        return st is not None and st.status == "ok"

    fixed_ok = [s for s in wanted.fixed if ok(s)]
    data_end = None
    if wanted.fixed and len(fixed_ok) == len(wanted.fixed):
        data_end = min(date.fromisoformat(state.symbols[s].last_date or "") for s in fixed_ok)
    if state.earnings_month is not None:
        earnings: Literal["complete", "partial", "none"] = "complete"
    elif state.earnings_done or (data_dir is not None and earnings_path(data_dir).exists()):
        earnings = "partial"
    else:
        earnings = "none"
    return BackfillStatus(
        fixed_done=len(fixed_ok), fixed_total=len(wanted.fixed),
        current_done=sum(ok(s) for s in wanted.current), current_total=len(wanted.current),
        delisted_done=sum(ok(s) for s in wanted.delisted), delisted_total=len(wanted.delisted),
        data_end=data_end,
        unavailable=sorted(s for s, st in state.symbols.items() if st.status != "ok"),
        quota=ledger.usage(), earnings=earnings,
    )  # fmt: skip


class BackfillRunner:
    """backtest-worker 空闲时调用 maybe_step：两次请求至少相隔 interval 秒，任何异常只记日志。"""

    def __init__(
        self,
        provider: DataProvider,
        store: ParquetStore,
        state: BackfillState,
        ledger: QuotaLedger,
        wanted: Callable[[], Wanted],
        cal: TradingCalendar,
        *,
        interval: float,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date],
        data_dir: Path | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.provider, self.store, self.state, self.ledger = provider, store, state, ledger
        self._wanted, self.cal, self.interval = wanted, cal, interval
        self._clock, self._today = clock, today
        self.data_dir, self.user_agent = data_dir, user_agent
        self._last: float | None = None

    def maybe_step(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self.interval:
            return
        self._last = now
        try:
            wanted, today = self._wanted(), self._today()
            r = backfill_step(self.provider, self.store, self.state, self.ledger, wanted, today,
                              self.cal)  # fmt: skip
            if r.outcome != "idle":
                log.info("回测数据 %s：%s", r.symbol, r.outcome)
            elif self.data_dir is not None:
                n = sync_earnings_step(self.state, self.data_dir, self.user_agent, wanted, today)
                if n:
                    log.info("财报日同步 %d 只", n)
        except Exception:
            log.exception("回测数据补齐出错（不影响回测）")


def make_runner(settings: Settings, cal: TradingCalendar) -> BackfillRunner | None:
    """服务器（backtest_source=tiingo 且配置了 key）上的补数器；否则为 None。"""
    if settings.data.backtest_source != "tiingo" or settings.tiingo_api_key is None:
        return None
    from datetime import UTC, datetime

    from quant.data.providers.tiingo import TiingoProvider
    from quant.data.universe import constituents_path, load_constituents

    data_dir = settings.data_dir
    ledger = QuotaLedger(quota_path(data_dir), settings.tiingo)

    def today() -> date:
        return cal.last_completed_trading_day(datetime.now(UTC))

    def wanted() -> Wanted:
        path = constituents_path(data_dir)
        return wanted_symbols(load_constituents(path) if path.exists() else None, today())

    ua = settings.edgar_user_agent
    return BackfillRunner(
        TiingoProvider(settings.tiingo_api_key, ledger), backtest_store(data_dir),
        BackfillState.load(state_path(data_dir)), ledger, wanted, cal,
        interval=3600 / settings.tiingo.hourly + 1, today=today, data_dir=data_dir,
        user_agent=ua.get_secret_value() if ua else None,
    )  # fmt: skip
