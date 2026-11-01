"""Tiingo 日线（#66）：服务器回测用的长周期含分红复权数据。

- 认证用请求头 Authorization: Token <key>，key 不出现在网址、日志与异常信息中。
- 每次请求先查额度台账；429 时把本小时记为用尽。
- 原始 open/high/low/close 未做任何调整，按 splitFactor 换算成“只做拆股调整”
  （统一格式，见 base.py）；
  adjClose 为拆股 + 分红复权，直接作为 adj_close。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

import numpy as np
import pandas as pd
from pydantic import SecretStr

from quant.data.providers.base import DataProvider, DataUnavailableError, normalize_bars
from quant.data.quota import QuotaExceeded, QuotaLedger

BASE_URL = "https://api.tiingo.com/tiingo/daily"


class SymbolNotFound(DataUnavailableError):
    """Tiingo 没有该代码（404 或空数组）：本月不再请求。"""


class TiingoAuthError(DataUnavailableError):
    """key 无效或无权限（401 / 403）：停止补数，属于配置问题。"""


Fetch = Callable[[str, Mapping[str, str]], tuple[int, Any]]


def http_fetch(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # 固定的 https 地址
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


class TiingoProvider(DataProvider):
    source: ClassVar[str] = "tiingo"

    def __init__(self, api_key: SecretStr, ledger: QuotaLedger, fetch: Fetch = http_fetch) -> None:
        self._key = api_key
        self.ledger = ledger
        self._fetch = fetch

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if start > end:
            raise ValueError(f"start {start} 晚于 end {end}")
        self.ledger.check(symbol)
        query = urlencode({"startDate": start.isoformat(), "endDate": end.isoformat()})
        # Tiingo 用连字符表示股票类别（BRK-B），成分名单与本地数据用点（BRK.B）
        url = f"{BASE_URL}/{quote(symbol.replace('.', '-'))}/prices?{query}"
        headers = {"Authorization": f"Token {self._key.get_secret_value()}",
                   "Content-Type": "application/json"}  # fmt: skip
        try:
            status, body = self._fetch(url, headers)
        except Exception as e:
            # 原始异常可能带有请求信息，只保留类型，避免 key 进入日志
            raise DataUnavailableError(f"{symbol}：Tiingo 请求失败（{type(e).__name__}）") from None
        self.ledger.record(symbol)
        if status == 429:
            self.ledger.mark_exhausted("hour")
            raise QuotaExceeded("hour")
        if status in (401, 403):
            raise TiingoAuthError(f"Tiingo 拒绝访问（{status}），请检查 TIINGO_API_KEY")
        if status == 404 or (status == 200 and not body):
            raise SymbolNotFound(f"{symbol}：Tiingo 没有该代码的日线")
        if status != 200 or not isinstance(body, list):
            raise DataUnavailableError(f"{symbol}：Tiingo 返回 {status}")
        return to_bars(body, start, end)


def to_bars(rows: list[Mapping[str, Any]], start: date, end: date) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["date"].str.slice(0, 10))
    df = df.sort_index()
    split = df["splitFactor"].astype(float).replace(0, 1.0)
    # 某日的累计因子 = 该日之后（不含该日）所有拆股因子的乘积
    after = np.cumprod(split.to_numpy()[::-1])[::-1]
    factor = pd.Series(np.append(after[1:], 1.0), index=df.index)
    out = pd.DataFrame(
        {
            "open": df["open"].astype(float) / factor,
            "high": df["high"].astype(float) / factor,
            "low": df["low"].astype(float) / factor,
            "close": df["close"].astype(float) / factor,
            "adj_close": df["adjClose"].astype(float),
            "volume": (df["volume"].astype(float) * factor).round(),
        },
        index=df.index,
    )
    return normalize_bars(out, start, end)
