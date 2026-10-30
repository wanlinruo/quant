"""拆股调整（#47 第二份计划任务 1）。"""

from datetime import date

import pandas as pd
import pytest

from quant.data.splits import split_adjust


def _bars(rows: list[tuple[str, float, int]]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([r[0] for r in rows], name="date")
    close = [r[1] for r in rows]
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close,
         "adj_close": [float("nan")] * len(rows), "volume": [r[2] for r in rows]},
        index=idx,
    )  # fmt: skip


def test_split_adjust_before_ex_date_only() -> None:
    # NVDA 2024-06-10 10 拆 1（乘数 0.1），新浪实测收盘价
    bars = _bars([("2024-06-07", 1208.88, 41238499), ("2024-06-10", 121.79, 314162647)])
    out = split_adjust(bars, [(date(2024, 6, 10), 0.1)])
    assert out.loc["2024-06-07", "close"] == pytest.approx(120.888)
    assert out.loc["2024-06-07", "high"] == pytest.approx(120.888)
    assert out.loc["2024-06-07", "volume"] == 412384990
    assert out.loc["2024-06-10", "close"] == pytest.approx(121.79)  # 除权日当天不变
    assert out.loc["2024-06-10", "volume"] == 314162647
    assert (out["adj_close"] == out["close"]).all()
    assert out["volume"].dtype == "int64"
    assert bars.loc["2024-06-07", "close"] == 1208.88  # 不修改输入


def test_split_adjust_multiple_splits_compound() -> None:
    bars = _bars(
        [("2024-01-02", 400.0, 100), ("2024-03-01", 200.0, 200), ("2024-06-03", 100.0, 400)]
    )
    out = split_adjust(bars, [(date(2024, 3, 1), 0.5), (date(2024, 6, 3), 0.5)])
    assert out["close"].tolist() == pytest.approx([100.0, 100.0, 100.0])
    assert out["volume"].tolist() == [400, 400, 400]


def test_split_adjust_no_splits() -> None:
    bars = _bars([("2024-01-02", 10.0, 5)])
    out = split_adjust(bars, [])
    assert out["close"].tolist() == [10.0] and out["adj_close"].tolist() == [10.0]
