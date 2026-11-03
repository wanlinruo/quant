"""Massive（原 Polygon.io）免费档：全市场日线与个股资料（#69，首页市场看板）。

- 免费档每分钟 5 次：两次请求至少相隔 min_interval 秒（默认 12.5）。
- 认证用请求头 Authorization: Bearer <key>，key 不出现在网址、日志与异常信息中。
- 全市场日线 adjusted=true 只做拆股调整（不含分红），用于展示涨跌与宽度。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
from pydantic import SecretStr

from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import Fetch, http_fetch
from quant.data.rate_limit import request_slot

BASE_URL = "https://api.massive.com"
COLUMNS = {"T": "symbol", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
           "vw": "vwap"}  # fmt: skip


class MassiveRateLimited(DataUnavailableError):
    """429：本分钟额度用尽，停止本轮。"""


class MassiveAuthError(DataUnavailableError):
    """401：key 无效。"""


@dataclass(frozen=True)
class Split:
    symbol: str
    execution_date: date
    split_from: float
    split_to: float

    @property
    def factor(self) -> float:
        """执行日之前的价格乘以该系数（10 拆 1 为 0.1），成交量除以它。"""
        return self.split_from / self.split_to


@dataclass(frozen=True)
class TickerDetails:
    name: str | None
    market_cap: float | None
    sic_description: str | None


class MassiveClient:
    def __init__(
        self,
        api_key: SecretStr,
        fetch: Fetch = http_fetch,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = 12.5,
        rate_limit_path: Path | None = None,
        stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self._key, self._fetch = api_key, fetch
        self._sleep, self._clock, self._min = sleep, clock, min_interval
        self._last: float | None = None
        self._rate_limit_path = rate_limit_path
        self._stop = stop

    def _get(self, path: str) -> tuple[int, Any]:
        if self._stop():
            raise DataUnavailableError("Massive 采集已停止")
        if self._rate_limit_path is not None:
            with request_slot(self._rate_limit_path, self._min):
                return self._request(path)
        return self._request(path)

    def _request(self, path: str) -> tuple[int, Any]:
        if self._stop():
            raise DataUnavailableError("Massive 采集已停止")
        if self._last is not None:
            wait = self._min - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        try:
            status, body = self._fetch(f"{BASE_URL}{path}", headers)
        except Exception as e:
            raise DataUnavailableError(f"Massive 请求失败（{type(e).__name__}）") from None
        finally:
            self._last = self._clock()
        if status == 429:
            raise MassiveRateLimited("Massive 额度已用尽（每分钟 5 次）")
        if status == 401:
            raise MassiveAuthError("Massive 拒绝访问（401），请检查 MASSIVE_API_KEY")
        if status == 403:
            # 免费档请求超出套餐的时间范围（如当日数据尚未就绪、2 年以前）时返回 403，不是 key 无效
            msg = body.get("message") if isinstance(body, dict) else None
            raise DataUnavailableError(
                f"Massive 403：{str(msg)[:120] if msg else '套餐不含该数据'}"
            )
        return status, body

    def grouped_daily(self, day: date) -> pd.DataFrame:
        """day 当天全部美股与 ETF 的日线；当天没有数据（休市或尚未发布）时返回空表。"""
        status, body = self._get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}?adjusted=true"
        )
        if status != 200 or not isinstance(body, dict):
            raise DataUnavailableError(f"Massive 全市场日线 {day} 返回 {status}")
        rows = body.get("results") or []
        df = pd.DataFrame(rows).rename(columns=COLUMNS)
        for c in COLUMNS.values():
            if c not in df.columns:
                df[c] = pd.Series(dtype="float64" if c != "symbol" else "object")
        return df[list(COLUMNS.values())].reset_index(drop=True)

    def splits(self, day: date) -> list[Split]:
        """day 当天执行的拆股 / 合股。"""
        status, body = self._get(
            f"/v3/reference/splits?execution_date={day.isoformat()}&limit=1000"
        )
        if status != 200 or not isinstance(body, dict):
            raise DataUnavailableError(f"Massive 拆股 {day} 返回 {status}")
        out = []
        for r in body.get("results") or []:
            try:
                f, t = float(r["split_from"]), float(r["split_to"])
                if f > 0 and t > 0:
                    out.append(
                        Split(str(r["ticker"]), date.fromisoformat(r["execution_date"]), f, t)
                    )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def ticker_details(self, symbol: str) -> TickerDetails | None:
        status, body = self._get(f"/v3/reference/tickers/{quote(symbol)}")
        if status == 404:
            return None
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("results"), dict):
            raise DataUnavailableError(f"Massive 个股资料 {symbol} 返回 {status}")
        r = body["results"]
        cap = r.get("market_cap")
        return TickerDetails(r.get("name"), float(cap) if cap else None, r.get("sic_description"))
