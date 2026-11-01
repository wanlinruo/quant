"""Finnhub 免费档：市场新闻、公司新闻、财报日历（#69 第 2 批）。

- 每分钟 60 次：两次请求至少相隔 min_interval 秒（默认 1.1）。
- key 只在请求头 X-Finnhub-Token，不出现在网址、日志与异常信息中。
- 401 为 key 无效；403 为付费接口，按“拿不到”处理。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode

from pydantic import SecretStr

from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import Fetch, http_fetch

BASE_URL = "https://finnhub.io/api/v1"


class FinnhubAuthError(DataUnavailableError):
    """401：key 无效。"""


@dataclass(frozen=True)
class NewsRaw:
    id: str
    time: datetime  # UTC
    headline: str
    source: str
    url: str
    related: tuple[str, ...]


@dataclass(frozen=True)
class EarningsRaw:
    symbol: str
    date: date
    hour: str  # "bmo" 盘前 | "amc" 盘后 | "" 未定
    eps_estimate: float | None
    eps_actual: float | None


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _news(rows: Any) -> list[NewsRaw]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            headline = str(r["headline"]).strip()
            t = datetime.fromtimestamp(int(r["datetime"]), UTC)
        except (KeyError, TypeError, ValueError):
            continue
        if not headline:
            continue
        url = str(r.get("url") or "")
        if not url.startswith(("https://", "http://")):
            url = ""  # 只接受网页链接（防止 javascript: 等进入页面）
        related = tuple(s.strip() for s in str(r.get("related") or "").split(",") if s.strip())
        out.append(NewsRaw(str(r.get("id", "")), t, headline, str(r.get("source") or ""),
                           url, related))  # fmt: skip
    return out


class FinnhubClient:
    def __init__(
        self,
        api_key: SecretStr,
        fetch: Fetch = http_fetch,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = 1.1,
    ) -> None:
        self._key, self._fetch = api_key, fetch
        self._sleep, self._clock, self._min = sleep, clock, min_interval
        self._last: float | None = None

    def _get(self, path: str, what: str, **params: str) -> Any:
        if self._last is not None:
            wait = self._min - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        headers = {"X-Finnhub-Token": self._key.get_secret_value()}
        try:
            status, body = self._fetch(f"{BASE_URL}{path}?{urlencode(params, safe='.,')}", headers)
        except Exception as e:
            raise DataUnavailableError(f"Finnhub {what} 请求失败（{type(e).__name__}）") from None
        finally:
            self._last = self._clock()
        if status == 401:
            raise FinnhubAuthError("Finnhub 拒绝访问（401），请检查 FINNHUB_API_KEY")
        if status != 200:
            raise DataUnavailableError(f"Finnhub {what} 返回 {status}")
        return body

    def market_news(self) -> list[NewsRaw]:
        return _news(self._get("/news", "市场新闻", category="general"))

    def company_news(self, symbol: str, start: date, end: date) -> list[NewsRaw]:
        body = self._get("/company-news", f"{symbol} 新闻", symbol=symbol,
                         **{"from": start.isoformat(), "to": end.isoformat()})  # fmt: skip
        return _news(body)

    def earnings(self, day: date) -> list[EarningsRaw]:
        """day 当天的财报（免费档一次最多 1500 条，所以按天查）。"""
        d = day.isoformat()
        body = self._get("/calendar/earnings", f"财报 {d}", **{"from": d, "to": d})
        rows = body.get("earningsCalendar") if isinstance(body, dict) else None
        out = []
        for r in rows if isinstance(rows, list) else []:
            try:
                out.append(EarningsRaw(str(r["symbol"]), date.fromisoformat(r["date"]),
                                       str(r.get("hour") or ""), _num(r.get("epsEstimate")),
                                       _num(r.get("epsActual"))))  # fmt: skip
            except (KeyError, TypeError, ValueError):
                continue
        return out
