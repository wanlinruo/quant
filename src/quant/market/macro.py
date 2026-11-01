"""行情条的宏观项（#69 第 2 批）：VIX、美债 10 年、美元、原油（FRED）与比特币（Coinbase）。

FRED 的日频序列次日才更新，每项带自己的数据日期，页面在与快照日期不同时注明。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from quant.data.providers.coinbase import Bar
from quant.data.providers.fred import Obs

# 键 → （FRED 序列, 标签, 注明）
MACRO_SERIES: dict[str, tuple[str, str, str]] = {
    "VIX": ("VIXCLS", "VIX", ""),
    "US10Y": ("DGS10", "美债 10 年", ""),
    "USD": ("DTWEXBGS", "美元指数", "广义美元指数 · 周"),
    "WTI": ("DCOILWTICO", "原油", "WTI"),
}
YIELDS = {"US10Y"}
SPARK = 30


@dataclass(frozen=True)
class MacroItem:
    symbol: str
    label: str
    close: float | None
    change: float | None
    change_pct: float | None
    spark: list[float]
    as_of: str | None
    note: str
    unit: str  # "price" | "yield"（收益率的涨跌按基点显示，不给百分比）


def macro_item(
    symbol: str, label: str, note: str, points: Sequence[tuple[date, float]], unit: str = "price"
) -> MacroItem:
    pts = sorted(points)
    last = pts[-1][1]
    prev = pts[-2][1] if len(pts) >= 2 else None
    change = last - prev if prev is not None else None
    pct = change / prev if change is not None and prev and unit == "price" else None
    return MacroItem(symbol, label, last, change, pct, [v for _, v in pts[-SPARK:]],
                     pts[-1][0].isoformat(), note, unit)  # fmt: skip


def build_macro(fred: Mapping[str, list[Obs]], btc: list[Bar] | None) -> list[MacroItem]:
    """按 VIX、美债 10 年、美元、原油、比特币的顺序；没有数据的项不出现。"""
    out = []
    for key, (series, label, note) in MACRO_SERIES.items():
        obs = fred.get(series) or []
        if obs:
            unit = "yield" if key in YIELDS else "price"
            out.append(macro_item(key, label, note, [(o.date, o.value) for o in obs], unit))
    if btc:
        pts = [(b.date, b.close) for b in btc]
        out.append(macro_item("BTC", "比特币", "Coinbase · UTC 日线", pts))
    return out
