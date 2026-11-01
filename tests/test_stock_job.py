"""每日任务 stock_data：个股新闻与财务指标（#70 第 3 批 Task 3）。假客户端，不访问网络。"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant.data import market_store as ms
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.finnhub import FinnhubAuthError, NewsRaw
from quant.market.stock_job import METRIC_KEYS, run_stock_data

TODAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 31, 1, 15, tzinfo=UTC)
T = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)


class FakeFinnhub:
    def __init__(
        self, fail_at: int | None = None, exc: type[Exception] = DataUnavailableError
    ) -> None:
        self.fail_at, self.exc = fail_at, exc
        self.news_calls: list[str] = []
        self.metric_calls: list[str] = []

    def _tick(self) -> None:
        if (
            self.fail_at is not None
            and len(self.news_calls) + len(self.metric_calls) >= self.fail_at
        ):
            raise self.exc("stop")

    def company_news(self, symbol: str, start: date, end: date) -> list[NewsRaw]:
        self._tick()
        self.news_calls.append(symbol)
        items = [NewsRaw(f"{symbol}{i}", T - timedelta(hours=i), f"{symbol} headline {i}", "CNBC",
                         f"https://n/{symbol}/{i}", (symbol,)) for i in range(20)]  # fmt: skip
        items.append(NewsRaw("dup", T, f"{symbol} HEADLINE 0", "Yahoo", "https://n/dup", (symbol,)))
        items.append(NewsRaw("e", T + timedelta(minutes=1), f"{symbol} Q3 earnings beat", "Yahoo",
                             "https://n/e", (symbol,)))  # fmt: skip
        return items

    def metrics(self, symbol: str, keys: Sequence[str]) -> dict[str, float | None]:
        self._tick()
        self.metric_calls.append(symbol)
        return {k: 1.0 for k in METRIC_KEYS}


def gics(data_dir: Path, syms: list[str]) -> None:
    p = ms.gics_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{s},Co {s},Information Technology,Hardware\n" for s in syms)
    p.write_text("Symbol,Security,GICS Sector,GICS Sub-Industry\n" + rows, encoding="utf-8")


def run(data_dir: Path, fh: FakeFinnhub, holdings: set[str] | None = None, today: date = TODAY,
        metrics_per_run: int = 120) -> str:  # fmt: skip
    return run_stock_data(data_dir=data_dir, today=today, now=NOW, holdings=holdings or set(),
                          finnhub=fh, metrics_per_run=metrics_per_run)  # fmt: skip


def doc(data_dir: Path, sym: str) -> dict[str, Any]:
    p = ms.stock_path(data_dir, sym)
    assert p is not None
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_stock_data_writes_files(tmp_path: Path) -> None:
    gics(tmp_path, ["S0", "S1", "S2"])
    summary = run(tmp_path, FakeFinnhub(), holdings={"HOLD"})
    for s in ("S0", "S1", "S2", "HOLD"):
        d = doc(tmp_path, s)
        assert d["symbol"] == s and d["metrics_updated"] == TODAY.isoformat()
        assert set(d["metrics"]) == set(METRIC_KEYS)
        news = d["news"]
        assert len(news) == 15 and news[0]["category"] == "earnings" and news[0]["tags"] == [s]
        assert [n["time"] for n in news] == sorted((n["time"] for n in news), reverse=True)
        assert len({n["headline"].lower() for n in news}) == 15
    assert "新闻 4 只" in summary and "指标 4 只" in summary


def test_stock_data_holdings_first_and_metrics_cap(tmp_path: Path) -> None:
    gics(tmp_path, [f"S{i}" for i in range(10)])
    fh = FakeFinnhub()
    run(tmp_path, fh, holdings={"H"}, metrics_per_run=3)
    assert fh.metric_calls == ["H", "S0", "S1"] and fh.news_calls[0] == "H"
    fh2 = FakeFinnhub()
    run(tmp_path, fh2, holdings={"H"}, metrics_per_run=3, today=TODAY + timedelta(days=1))
    assert fh2.metric_calls == ["S2", "S3", "S4"]  # 已取过的 6 天内不重取


def test_stock_data_resumes_oldest_first(tmp_path: Path) -> None:
    gics(tmp_path, [f"S{i}" for i in range(6)])
    summary = run(tmp_path, FakeFinnhub(fail_at=3))
    assert "限流或故障" in summary
    assert sorted(p.stem for p in (tmp_path / "market" / "stocks").glob("*.json")) == [
        "S0",
        "S1",
        "S2",
    ]
    fh = FakeFinnhub()
    run(tmp_path, fh, today=TODAY + timedelta(days=1))
    assert fh.news_calls[:3] == ["S3", "S4", "S5"]


def test_stock_data_auth_error_raises(tmp_path: Path) -> None:
    gics(tmp_path, ["S0"])
    with pytest.raises(FinnhubAuthError):
        run(tmp_path, FakeFinnhub(fail_at=0, exc=FinnhubAuthError))


def test_stock_data_skips_etf_and_bad_symbols(tmp_path: Path) -> None:
    gics(tmp_path, ["S0"])
    fh = FakeFinnhub()
    run(tmp_path, fh, holdings={"QQQ", "bad sym"})
    assert fh.news_calls == ["S0"] and fh.metric_calls == ["S0"]


def test_stock_path_validation() -> None:
    assert ms.stock_path(Path("/d"), "BRK.B") == Path("/d/market/stocks/BRK.B.json")
    assert ms.stock_path(Path("/d"), "../etc") is None
    assert ms.stock_path(Path("/d"), "aapl") is None and ms.stock_path(Path("/d"), "A" * 11) is None


def test_scheduler_includes_stock_data() -> None:
    from quant.core.calendar import get_calendar
    from quant.core.settings import ScheduleSettings
    from quant.engine.executor import ET
    from quant.jobs.daily import JOB_FUNCS, JOBS
    from quant.jobs.scheduler import day_plan

    plan = dict(day_plan(TODAY, ScheduleSettings(), get_calendar()))
    assert plan["stock_data"] == datetime(2026, 10, 30, 21, 15, tzinfo=ET)
    assert "stock_data" in JOBS and "stock_data" in JOB_FUNCS
