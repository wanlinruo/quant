"""成分股快照（#47 Task 1）。"""

import os
import pickle
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.data.providers.base import DataUnavailableError
from quant.data.universe import (
    constituents_path,
    parse_sp500_history,
    stale_warning,
    sync_constituents,
)
from quant.engine.backtester import Backtester
from quant.strategies.base import Context, Strategy
from quant.strategies.universe import Constituents
from test_backtester import sample_bars

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


class Recorder(Strategy):
    name = "recorder"

    def __init__(self) -> None:
        self.seen: list[Constituents | None] = []

    def target_weights(self, ctx: Context) -> dict[str, float]:
        self.seen.append(ctx.universe)
        return {}


def test_backtester_passes_universe_to_context() -> None:
    cal = get_calendar()
    u = parse_sp500_history(TEXT)
    strat = Recorder()
    bt = Backtester(
        strat,
        {"SPY": sample_bars(cal, 1, 0.0, 0.0)},
        cal,
        CostModel(Decimal("0.005"), Decimal("1"), Decimal("0.0005")),
        benchmark=None,
        universe=u,
    )
    bt.run(date(2024, 1, 2), date(2024, 1, 4))
    assert strat.seen and all(x is u for x in strat.seen)


# ---- 成分名单快照同步（第二份计划任务 2） ----

TODAY = date(2026, 10, 30)


class Fetch:
    def __init__(self, result: bytes | Exception) -> None:
        self.result = result
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _age(path: Path, days: int) -> None:
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_constituents_path(tmp_path: Path) -> None:
    assert constituents_path(tmp_path) == tmp_path / "universe" / "sp500_history.csv"


def test_sync_downloads_when_missing(tmp_path: Path) -> None:
    path = constituents_path(tmp_path)
    fetch = Fetch(TEXT.encode())
    u = sync_constituents(path, TODAY, fetch=fetch)
    assert len(fetch.urls) == 1 and path.exists()
    assert u.members(date(2015, 3, 2)) == {"AAA", "CCC", "DDD"}


def test_sync_skips_fresh_file(tmp_path: Path) -> None:
    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(TEXT, encoding="utf-8")
    fetch = Fetch(b"never")
    sync_constituents(path, TODAY, fetch=fetch)
    assert fetch.urls == []


def test_sync_falls_back_to_old_file_on_download_error(tmp_path: Path) -> None:
    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(TEXT, encoding="utf-8")
    _age(path, 10)
    u = sync_constituents(path, date.today(), fetch=Fetch(OSError("timeout")))
    assert u.members(date(2015, 1, 2)) == {"AAA", "BBB", "CCC"}


def test_sync_rejects_unparsable_download(tmp_path: Path) -> None:
    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(TEXT, encoding="utf-8")
    _age(path, 10)
    with pytest.raises(ValueError):
        sync_constituents(path, date.today(), fetch=Fetch(b"<html>not csv</html>"))
    assert path.read_text(encoding="utf-8") == TEXT  # 原文件不变


def test_sync_without_any_file_and_download_error(tmp_path: Path) -> None:
    with pytest.raises(DataUnavailableError):
        sync_constituents(constituents_path(tmp_path), TODAY, fetch=Fetch(OSError("x")))


def test_stale_warning() -> None:
    u = Constituents({date(2026, 7, 1): frozenset({"A"})})
    assert stale_warning(u, date(2026, 10, 30)) is not None  # 121 天
    assert stale_warning(u, date(2026, 10, 28)) is None  # 119 天
