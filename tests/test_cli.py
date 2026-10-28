"""命令行回测端到端测试：假数据源 + 临时数据目录，不访问网络、不写数据库。"""

import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.cli import build_parser, main, run_backtest
from quant.core.calendar import get_calendar
from quant.core.settings import Settings
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider, normalize_bars
from quant.data.store import ParquetStore

SYMS = {"SPY": 0.0004, "QQQ": 0.0006, "TLT": 0.0001, "GLD": 0.0002, "SHY": 0.00008}


class FakeProvider(DataProvider):
    source = "fake"

    def __init__(self, broken: str | None = None) -> None:
        self.calls: list[str] = []
        self.broken = broken

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append(symbol)
        cal = get_calendar()
        days = pd.DatetimeIndex(cal.trading_days(start, end))
        rng = np.random.default_rng(sum(map(ord, symbol)))
        adj = 100 * np.cumprod(1 + rng.normal(SYMS[symbol], 0.01, len(days)))
        df = pd.DataFrame(
            {"open": adj, "high": adj * 1.01, "low": adj * 0.99, "close": adj, "adj_close": adj,
             "volume": np.full(len(days), 1000, dtype="int64")},
            index=days,
        )  # fmt: skip
        if symbol == self.broken:
            df = df.drop(df.index[10])  # 缺一个交易日
        return normalize_bars(df)


def settings(tmp_path: Path) -> Settings:
    return Settings(broker="sim", data_dir=tmp_path, TRADING_MODE="sim")


ARGS = ["backtest", "--start", "2024-01-02", "--end", "2025-12-31",
        "--commission-per-share", "0.005", "--min-commission", "1", "--no-db"]  # fmt: skip


def test_end_to_end_backtest(tmp_path: Path) -> None:
    provider, out = FakeProvider(), io.StringIO()
    args = build_parser().parse_args([*ARGS, "--param", "lookback=60"])
    result = run_backtest(args, settings(tmp_path), provider, out)
    text = out.getvalue()
    assert "年化收益" in text and "最大回撤" in text and "基准 SPY" in text
    assert "未写入数据库" in text
    assert result.metrics.trades > 0
    assert sorted(provider.calls) == sorted(SYMS)
    assert ParquetStore(tmp_path).symbols() == sorted(SYMS)

    # 第二次运行直接使用本地 Parquet，不再拉取
    provider2 = FakeProvider()
    run_backtest(args, settings(tmp_path), provider2, io.StringIO())
    assert provider2.calls == []


def test_validation_failure_stops(tmp_path: Path) -> None:
    args = build_parser().parse_args(ARGS)
    with pytest.raises(DataValidationError, match="missing_day"):
        run_backtest(args, settings(tmp_path), FakeProvider(broken="TLT"), io.StringIO())


def test_commission_required(tmp_path: Path) -> None:
    args = build_parser().parse_args(["backtest", "--start", "2024-01-02", "--end", "2025-12-31"])
    with pytest.raises(ValueError, match="#5"):
        run_backtest(args, settings(tmp_path), FakeProvider(), io.StringIO())


def test_param_parsing() -> None:
    args = build_parser().parse_args(
        [*ARGS, "--param", "lookback=60", "--param", "universe=SPY,TLT", "--cash", "5000"]
    )
    assert dict(args.param) == {"lookback": 60, "universe": ["SPY", "TLT"]}
    assert args.cash == Decimal("5000")
    with pytest.raises(SystemExit):
        build_parser().parse_args([*ARGS, "--param", "novalue"])


def test_main_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRADING_MODE", "bogus")
    assert main(ARGS) == 1
    assert "TRADING_MODE" in capsys.readouterr().err
