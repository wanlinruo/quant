from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.data.providers.base import BAR_COLUMNS, normalize_bars
from quant.data.store import ParquetStore
from quant.strategies.base import Context

T1 = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 29, 21, 0, tzinfo=UTC)


def bars(dates: list[str], base: float = 100.0) -> pd.DataFrame:
    n = len(dates)
    close = base + np.arange(n, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "adj_close": close * 0.9,
            "volume": np.full(n, 1000, dtype="int64"),
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


def test_layout_and_roundtrip(store: ParquetStore, tmp_path: Path) -> None:
    df = bars(["2026-10-26", "2026-10-27"])
    assert store.write_bars("SPY", df, "yfinance", T1) == 2
    assert (tmp_path / "bars/daily/symbol=SPY/data.parquet").is_file()
    got = store.read_bars("SPY")
    assert tuple(got.columns) == BAR_COLUMNS
    pd.testing.assert_frame_equal(got, normalize_bars(df), check_freq=False)
    raw = pd.read_parquet(tmp_path / "bars/daily/symbol=SPY/data.parquet")
    assert list(raw.columns) == [
        "date", "open", "high", "low", "close", "adj_close", "volume", "source", "ingested_at",
    ]  # fmt: skip


def test_rewrite_same_data_is_noop(store: ParquetStore, tmp_path: Path) -> None:
    df = bars(["2026-10-26", "2026-10-27"])
    store.write_bars("SPY", df, "yfinance", T1)
    path = tmp_path / "bars/daily/symbol=SPY/data.parquet"
    before = path.read_bytes()
    assert store.write_bars("SPY", df, "yfinance", T2) == 0
    assert path.read_bytes() == before  # 文件完全不变
    assert (store.ingestion_info("SPY")["ingested_at"] == pd.Timestamp(T1)).all()


def test_merge_appends_and_updates(store: ParquetStore) -> None:
    store.write_bars("SPY", bars(["2026-10-26", "2026-10-27"]), "yfinance", T1)
    update = bars(["2026-10-27", "2026-10-28"], base=200.0)  # 修订 10-27，新增 10-28
    assert store.write_bars("SPY", update, "yfinance", T2) == 2
    got = store.read_bars("SPY")
    assert len(got) == 3
    assert got.loc["2026-10-26", "close"] == 100.0
    assert got.loc["2026-10-27", "close"] == 200.0
    info = store.ingestion_info("SPY")
    assert info.loc["2026-10-26", "ingested_at"] == pd.Timestamp(T1)
    assert info.loc["2026-10-27", "ingested_at"] == pd.Timestamp(T2)


def test_nan_adj_close_counts_as_unchanged(store: ParquetStore) -> None:
    df = bars(["2026-10-27"])
    df["adj_close"] = np.nan  # 老虎数据没有 adj_close
    store.write_bars("SPY", df, "tiger", T1)
    assert store.write_bars("SPY", df, "tiger", T2) == 0


def test_source_change_is_update(store: ParquetStore) -> None:
    df = bars(["2026-10-27"])
    store.write_bars("SPY", df, "yfinance", T1)
    assert store.write_bars("SPY", df, "tiger", T2) == 1
    assert store.ingestion_info("SPY")["source"].tolist() == ["tiger"]


def test_read_range_and_missing(store: ParquetStore) -> None:
    store.write_bars("SPY", bars(["2026-10-26", "2026-10-27", "2026-10-28"]), "yfinance", T1)
    got = store.read_bars("SPY", start=date(2026, 10, 27), end=date(2026, 10, 27))
    assert len(got) == 1
    assert store.read_bars("QQQ").empty
    assert store.symbols() == ["SPY"]


def test_no_temp_files_left(store: ParquetStore, tmp_path: Path) -> None:
    store.write_bars("SPY", bars(["2026-10-26"]), "yfinance", T1)
    store.write_bars("SPY", bars(["2026-10-27"]), "yfinance", T2)
    leftovers = [
        p for p in (tmp_path / "bars/daily/symbol=SPY").iterdir() if p.name != "data.parquet"
    ]
    assert leftovers == []


def test_invalid_symbol(store: ParquetStore) -> None:
    with pytest.raises(ValueError):
        store.write_bars("../etc", bars(["2026-10-26"]), "yfinance", T1)


def test_prices_wide_and_context_loader(store: ParquetStore) -> None:
    store.write_bars("SPY", bars(["2026-10-26", "2026-10-27", "2026-10-28"]), "yfinance", T1)
    store.write_bars("TLT", bars(["2026-10-27", "2026-10-28"], base=90), "yfinance", T1)
    wide = store.prices(["SPY", "TLT"])
    assert list(wide.columns) == ["SPY", "TLT"]
    assert pd.isna(wide.loc["2026-10-26", "TLT"])
    assert wide.loc["2026-10-28", "SPY"] == pytest.approx(102 * 0.9)
    # 直接作为策略 Context 的数据源，as_of 之后的数据不可见
    ctx = Context(date(2026, 10, 27), store.prices)
    assert ctx.bars(["SPY"]).index.max() == pd.Timestamp("2026-10-27")
    with pytest.raises(ValueError):
        store.prices(["SPY"], field="foo")


def test_duckdb_query(store: ParquetStore) -> None:
    assert store.query("SELECT count(*) AS n FROM bars")["n"].iloc[0] == 0
    store.write_bars("SPY", bars(["2026-10-26", "2026-10-27"]), "yfinance", T1)
    store.write_bars("QQQ", bars(["2026-10-27"]), "yfinance", T1)
    df = store.query(
        "SELECT symbol, count(*) AS n, max(date) AS last FROM bars GROUP BY symbol ORDER BY symbol"
    )
    assert df["symbol"].tolist() == ["QQQ", "SPY"]
    assert df["n"].tolist() == [1, 2]
