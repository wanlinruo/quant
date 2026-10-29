"""数据校验的单日涨跌阈值按策略设置（个股财报日常有 15–30% 的波动）。"""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar
from quant.data.pipeline import DataValidationError, ensure_bars
from quant.data.providers.base import DataProvider, normalize_bars
from quant.data.store import ParquetStore
from quant.strategies import get_strategy

CAL = TradingCalendar()
DAYS = CAL.trading_days(date(2024, 1, 2), date(2024, 3, 28))


class Jumpy(DataProvider):
    source = "fake"

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        px = np.full(len(DAYS), 100.0)
        px[30:] = 125.0  # 财报日 +25%
        return normalize_bars(
            pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "adj_close": px,
                          "volume": np.full(len(DAYS), 1, dtype="int64")},
                         index=pd.DatetimeIndex(DAYS))
        )  # fmt: skip


def test_default_threshold_flags_25pct(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="jump"):
        ensure_bars(ParquetStore(tmp_path), Jumpy(), CAL, ["AAA"], DAYS[0], DAYS[-1])


def test_custom_threshold_allows_earnings_move(tmp_path: Path) -> None:
    out = ensure_bars(ParquetStore(tmp_path), Jumpy(), CAL, ["AAA"], DAYS[0], DAYS[-1],
                      max_move=0.40)  # fmt: skip
    assert len(out["AAA"]) == len(DAYS)


def test_strategy_thresholds() -> None:
    assert get_strategy("etf_momentum").max_daily_move == 0.15
    assert get_strategy("swing_pullback").max_daily_move == 0.40
