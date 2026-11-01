"""行情条宏观项（#69 第 2 批 Task 3）。"""

from datetime import date, timedelta

import pytest

from quant.data.providers.coinbase import Bar
from quant.data.providers.fred import Obs
from quant.market.macro import build_macro, macro_item


def test_macro_item_change_and_spark() -> None:
    pts = [(date(2026, 9, 1) + timedelta(days=i), 100.0 + i) for i in range(40)]
    it = macro_item("WTI", "原油", "WTI", pts)
    assert it.close == 139.0 and it.change == 1.0 and it.change_pct == pytest.approx(1 / 138)
    assert len(it.spark) == 30 and it.spark[-1] == 139.0 and it.as_of == "2026-10-10"


def test_yield_has_no_pct() -> None:
    pts = [(date(2026, 10, 28), 4.93), (date(2026, 10, 29), 4.98)]
    it = macro_item("US10Y", "美债 10 年", "", pts, unit="yield")
    assert it.change == pytest.approx(0.05) and it.change_pct is None and it.unit == "yield"


def test_single_point_has_no_change() -> None:
    it = macro_item("VIX", "VIX", "", [(date(2026, 10, 29), 17.2)])
    assert it.close == 17.2 and it.change is None and it.change_pct is None


def test_build_macro_order_and_missing() -> None:
    fred = {"VIXCLS": [Obs(date(2026, 10, 28), 17.0), Obs(date(2026, 10, 29), 17.2)],
            "DGS10": [Obs(date(2026, 10, 29), 4.98)], "DTWEXBGS": []}  # fmt: skip
    btc = [
        Bar(date(2026, 10, 30), 1, 1, 1, 77000.0, 1),
        Bar(date(2026, 10, 31), 1, 1, 1, 77756.72, 1),
    ]
    items = build_macro(fred, btc)
    assert [i.symbol for i in items] == ["VIX", "US10Y", "BTC"]
    assert items[1].unit == "yield" and items[2].note == "Coinbase · UTC 日线"
    assert items[2].label == "比特币" and items[2].as_of == "2026-10-31"
    assert build_macro({}, None) == []
