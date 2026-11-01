"""每日任务 market_data（#69 第 1 批 Task 4）。假 Massive 客户端，合成数据。"""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant.core.calendar import get_calendar
from quant.core.settings import BoardSettings
from quant.data import market_store as ms
from quant.data.market_store import snapshot_path
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.massive import MassiveAuthError, MassiveRateLimited, TickerDetails
from quant.market.job import run_market_data

CAL = get_calendar()
TODAY = date(2026, 10, 30)
GICS_CSV = "Symbol,Security,GICS Sector\n" + "".join(
    f"S{i},Co {i},Information Technology\n" for i in range(450)
)
SETTINGS = BoardSettings(history_days=8, history_per_run=3, caps_per_run=2, caps_max_age_days=7)


def frame(day: date) -> pd.DataFrame:
    syms = [*ms.ETF_SET, *(f"S{i}" for i in range(450)), "NOISE"]
    px = 100 + (day - date(2026, 1, 1)).days * 0.1
    return pd.DataFrame({"symbol": syms, "open": px, "high": px + 1, "low": px - 1, "close": px,
                         "volume": 1000.0, "vwap": px})  # fmt: skip


class FakeClient:
    def __init__(self, empty: set[date] = frozenset(), limit_after: int | None = None,  # type: ignore[assignment]
                 auth: bool = False) -> None:  # fmt: skip
        self.empty, self.limit_after, self.auth = empty, limit_after, auth
        self.days: list[date] = []
        self.details: list[str] = []

    def _tick(self) -> None:
        if self.auth:
            raise MassiveAuthError("bad key")
        if self.limit_after is not None and len(self.days) + len(self.details) >= self.limit_after:
            raise MassiveRateLimited("429")

    def grouped_daily(self, day: date) -> pd.DataFrame:
        self._tick()
        self.days.append(day)
        return frame(day).iloc[0:0] if day in self.empty else frame(day)

    def ticker_details(self, symbol: str) -> TickerDetails | None:
        self._tick()
        self.details.append(symbol)
        return TickerDetails(f"Co {symbol}", 1e9, None)


def run(tmp_path: Path, client: FakeClient, today: date = TODAY) -> str:
    return run_market_data(client, tmp_path, today, CAL, SETTINGS, holdings={"HOLD1"},
                           fetch_text=lambda url: GICS_CSV)  # fmt: skip


def snapshot(tmp_path: Path) -> dict[str, object]:
    return json.loads(snapshot_path(tmp_path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_job_first_run_builds_snapshot(tmp_path: Path) -> None:
    c = FakeClient()
    run(tmp_path, c)
    assert c.days[0] == TODAY and len(c.days) == 1 + 3  # 当日 + 补 3 天历史
    prev = CAL.previous_trading_day(TODAY)
    assert c.days[1] == prev  # 由近及远
    snap = snapshot(tmp_path)
    assert snap["as_of"] == TODAY.isoformat() and snap["stale"] is False
    assert c.details == ["S0", "S1"]  # 每次最多 2 只市值
    store = ms.MarketStore(tmp_path)
    assert "NOISE" not in set(pd.read_parquet(store.root / "daily" / f"{TODAY}.parquet")["symbol"])


def test_job_backfills_until_history_days(tmp_path: Path) -> None:
    c = FakeClient()
    for _ in range(4):
        run(tmp_path, c)
    assert len(ms.MarketStore(tmp_path).days()) == SETTINGS.history_days


def test_job_keeps_previous_snapshot_when_day_empty(tmp_path: Path) -> None:
    prev = CAL.previous_trading_day(TODAY)
    run(tmp_path, FakeClient(), today=prev)
    run(tmp_path, FakeClient(empty={TODAY}))
    snap = snapshot(tmp_path)
    assert snap["stale"] is True and snap["as_of"] == prev.isoformat()


def test_job_stops_backfill_on_rate_limit(tmp_path: Path) -> None:
    c = FakeClient(limit_after=2)
    summary = run(tmp_path, c)
    assert len(c.days) == 2 and c.details == []
    assert "限流" in summary
    assert snapshot(tmp_path)["as_of"] == TODAY.isoformat()


def test_job_auth_error_raises(tmp_path: Path) -> None:
    with pytest.raises(MassiveAuthError):
        run(tmp_path, FakeClient(auth=True))


def test_job_refreshes_oldest_caps_first(tmp_path: Path) -> None:
    store = ms.MarketStore(tmp_path)
    store.write_caps(
        {"S0": ms.CapInfo(1.0, "x", TODAY), "S1": ms.CapInfo(1.0, "x", date(2026, 10, 1))}
    )
    never = sorted(f"S{i}" for i in range(2, 450))  # 从未取过的，按成分表顺序（字符串排序）
    c = FakeClient()
    run(tmp_path, c)
    assert c.details == never[:2]  # 从未取过的先取；S0 是新的、S1 排在它们之后
    run(tmp_path, c)
    assert c.details[2:] == never[2:4]


def test_snapshot_written_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run(tmp_path, FakeClient())
    before = snapshot_path(tmp_path).read_text(encoding="utf-8")
    import quant.market.job as job

    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(job, "atomic_write_text", boom)
    with pytest.raises(OSError):
        run(tmp_path, FakeClient())
    assert snapshot_path(tmp_path).read_text(encoding="utf-8") == before


def test_gics_unavailable_without_previous_raises(tmp_path: Path) -> None:
    def boom(url: str) -> str:
        raise OSError("down")

    with pytest.raises(DataUnavailableError):
        run_market_data(FakeClient(), tmp_path, TODAY, CAL, SETTINGS, holdings=set(),
                        fetch_text=boom)  # fmt: skip


def test_scheduler_includes_market_data() -> None:
    from datetime import datetime

    from quant.core.settings import ScheduleSettings
    from quant.engine.executor import ET
    from quant.jobs.daily import JOB_FUNCS, JOBS
    from quant.jobs.scheduler import day_plan

    plan = dict(day_plan(TODAY, ScheduleSettings(), CAL))
    assert plan["market_data"] == datetime(2026, 10, 30, 20, 30, tzinfo=ET)
    assert "market_data" in JOBS and "market_data" in JOB_FUNCS


def test_job_without_key_skips(tmp_path: Path) -> None:
    from quant.core.settings import Settings
    from quant.jobs.daily import market_data

    class Ctx:
        settings = Settings(broker="sim", TRADING_MODE="sim", data_dir=tmp_path)
        engine = None

    assert "未配置" in market_data(Ctx(), TODAY)  # type: ignore[arg-type]
