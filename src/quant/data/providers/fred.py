"""FRED（圣路易斯联储）免费接口：宏观序列（#69 第 2 批，行情条宏观项与经济数据实际值）。

- 每分钟 120 次：两次请求至少相隔 min_interval 秒（默认 0.6）。
- FRED 只支持把 key 放在网址参数里，所以异常信息一律不带网址与 key（from None）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from pydantic import SecretStr

from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import Fetch, http_fetch

BASE_URL = "https://api.stlouisfed.org/fred"


class FredUnreachable(DataUnavailableError):
    """连不上 FRED（网络错误、超时、5xx、429）：本轮后续请求也会失败，调用方应停止。"""


class FredAuthError(DataUnavailableError):
    """key 无效（FRED 返回 400 并提示 api_key）。"""


class FredHttpError(DataUnavailableError):
    """单个 FRED 请求的非认证 4xx；只影响对应序列。"""


class FredParseError(DataUnavailableError):
    """FRED 200 响应结构或字段无法解析；只影响对应序列。"""


@dataclass(frozen=True)
class Obs:
    date: date
    value: float


class FredClient:
    def __init__(
        self,
        api_key: SecretStr,
        fetch: Fetch = http_fetch,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = 0.6,
    ) -> None:
        self._key, self._fetch = api_key, fetch
        self._sleep, self._clock, self._min = sleep, clock, min_interval
        self._last: float | None = None

    def _get(self, path: str, what: str, **params: str) -> Any:
        if self._last is not None:
            wait = self._min - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        query = urlencode({**params, "api_key": self._key.get_secret_value(), "file_type": "json"})
        try:
            status, body = self._fetch(f"{BASE_URL}{path}?{query}", {})
        except Exception as e:
            raise FredUnreachable(f"FRED {what} 请求失败（{type(e).__name__}）") from None
        finally:
            self._last = self._clock()
        if status == 400 and isinstance(body, dict) and "api_key" in str(body.get("error_message")):
            raise FredAuthError("FRED 拒绝访问，请检查 FRED_API_KEY")
        if status >= 500 or status == 429:
            raise FredUnreachable(f"FRED {what} 返回 {status}")
        if status != 200:
            raise FredHttpError(f"FRED {what} 返回 {status}")
        if not isinstance(body, dict):
            raise FredParseError(f"FRED {what} 响应格式错误")
        return body

    def observations(self, series: str, start: date) -> list[Obs]:
        """series 自 start 起的观测值，升序；缺失值（"."）跳过。"""
        body = self._get("/series/observations", series, series_id=series,
                         observation_start=start.isoformat())  # fmt: skip
        rows = body.get("observations")
        if not isinstance(rows, list):
            raise FredParseError(f"FRED {series} observations 格式错误")
        out = []
        for r in rows:
            if isinstance(r, dict) and r.get("value") == ".":
                continue
            try:
                out.append(Obs(date.fromisoformat(r["date"]), float(r["value"])))
            except (KeyError, TypeError, ValueError):
                raise FredParseError(f"FRED {series} 观测值无法解析") from None
        return sorted(out, key=lambda o: o.date)

    def last_updated(self, series: str) -> datetime:
        """序列最近一次更新的时间（带时区）。"""
        body = self._get("/series", series, series_id=series)
        try:
            raw = str(body["seriess"][0]["last_updated"])
            if len(raw) >= 3 and raw[-3] in "+-" and raw[-2:].isdigit():
                raw += ":00"  # "-05" → "-05:00"
            return datetime.fromisoformat(raw)
        except (KeyError, IndexError, TypeError, ValueError):
            raise FredParseError(f"FRED {series} 更新时间无法解析") from None
