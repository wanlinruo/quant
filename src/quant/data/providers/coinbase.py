"""Coinbase 公开行情：比特币日线（#69 第 2 批，行情条），无需 key。日期为 UTC。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import Fetch, http_fetch

URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def btc_daily(fetch: Fetch = http_fetch) -> list[Bar]:
    """最近约 300 天的 BTC-USD 日线，升序。返回格式为 [[时间, 低, 高, 开, 收, 量], ...]。"""
    try:
        status, body = fetch(URL, {"User-Agent": "quant-dashboard"})
    except Exception as e:
        raise DataUnavailableError(f"Coinbase 请求失败（{type(e).__name__}）") from None
    if status != 200 or not isinstance(body, list):
        raise DataUnavailableError(f"Coinbase 返回 {status}")
    out = []
    for r in body:
        try:
            t, lo, hi, op, cl, vol = r[:6]
            day = datetime.fromtimestamp(int(t), UTC).date()
            out.append(Bar(day, float(op), float(hi), float(lo), float(cl), float(vol)))
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda b: b.date)
