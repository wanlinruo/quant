"""标普 500 GICS 成分表与首页市场数据存储（#69 第 1 批 Task 2）。合成数据，不访问网络。"""

import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from quant.data import market_store as ms
from quant.data.providers.base import DataUnavailableError

HEADER = (
    "Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded\n"
)
ROWS = [
    'AAPL,Apple Inc.,Information Technology,Hardware,"Cupertino, CA",1982-11-30,320193,1977',
    'BRK.B,Berkshire Hathaway,Financials,Holdings,"Omaha, NE",2010-02-16,1067983,1839',
    'XOM,ExxonMobil,Energy,Oil & Gas,"Spring, TX",1957-03-04,34088,1999',
]


def csv(n: int = 3) -> str:
    rows = (ROWS * (n // 3 + 1))[:n] if n > 3 else ROWS
    if n > 3:  # 造足够多的不同代码
        rows = [r.replace(r.split(",")[0], f"S{i}", 1) for i, r in enumerate(rows)]
    return HEADER + "\n".join(rows) + "\n"


def test_parse_gics_and_sector_lookup() -> None:
    t = ms.parse_gics(csv())
    assert t.members == ["AAPL", "BRK.B", "XOM"]
    assert t.sector("BRK.B") == "Financials" and t.name("XOM") == "ExxonMobil"
    assert t.sector("ZZZZ") is None
    assert ms.SECTOR_ETF["Information Technology"] == "XLK" and len(ms.SECTOR_ETF) == 11


def test_sync_downloads_then_skips_when_fresh(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return csv(450)

    t = ms.sync_gics(tmp_path, date(2026, 11, 2), fetch)
    assert len(t.members) == 450 and calls == [ms.GICS_URL]
    ms.sync_gics(tmp_path, date(2026, 11, 3), fetch)
    assert len(calls) == 1  # 7 天内不再下载


def test_sync_keeps_previous_on_failure(tmp_path: Path) -> None:
    ms.sync_gics(tmp_path, date(2026, 11, 2), lambda url: csv(450))
    old = time.time() - 10 * 86400
    os.utime(ms.gics_path(tmp_path), (old, old))

    def boom(url: str) -> str:
        raise OSError("network down")

    assert len(ms.sync_gics(tmp_path, date(2026, 11, 20), boom).members) == 450
    assert (
        len(ms.sync_gics(tmp_path, date(2026, 11, 20), lambda url: "bad,csv\n1,2\n").members) == 450
    )
    assert (
        len(ms.sync_gics(tmp_path, date(2026, 11, 20), lambda url: csv(10)).members) == 450
    )  # 行数过少


def test_sync_without_previous_raises(tmp_path: Path) -> None:
    def boom(url: str) -> str:
        raise OSError("network down")

    with pytest.raises(DataUnavailableError):
        ms.sync_gics(tmp_path, date(2026, 11, 2), boom)


def day_frame(price: float) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["SPY", "AAPL", "NOISE"], "open": price, "high": price + 1, "low": price - 1,
        "close": price, "volume": 1000.0, "vwap": price,
    })  # fmt: skip


def test_write_day_filters_and_panel(tmp_path: Path) -> None:
    store = ms.MarketStore(tmp_path)
    for i, d in enumerate((date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30))):
        assert store.write_day(d, day_frame(100 + i), keep={"SPY", "AAPL"}) == 2
    assert store.days() == [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)]
    close = store.panel("close")
    assert list(close.columns) == ["AAPL", "SPY"] and close["SPY"].tolist() == [100, 101, 102]
    assert store.panel("close", ["SPY"], last_n=2)["SPY"].tolist() == [101, 102]
    assert (
        store.panel("close", ["MISSING"]).empty
        or store.panel("close", ["MISSING"]).isna().all().all()
    )


def test_caps_roundtrip(tmp_path: Path) -> None:
    store = ms.MarketStore(tmp_path)
    assert store.caps() == {}
    now = datetime(2026, 11, 2, tzinfo=UTC).date()
    store.write_caps({"AAPL": ms.CapInfo(5.0e12, "Apple Inc.", now)})
    got = store.caps()
    assert got["AAPL"].market_cap == 5.0e12 and got["AAPL"].updated == now
