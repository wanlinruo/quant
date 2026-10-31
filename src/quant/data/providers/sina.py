"""新浪美股日 K（#41）：与主数据源抽样比对收盘价；组合策略的候选池日线（#47，见 universe_bars）。

接口为新浪财经网站前端使用的 JSONP 接口，非官方 API，条款上仅限个人使用，随时可能变化。
返回未做分红调整的日线，adj_close 为 NaN；比对只用 close（data/validate.py compare_sources）。
东京服务器可访问（Yahoo 被限流，见 #7）。候选池一次约 500 只连续请求，相邻两次至少间隔
MIN_CALL_INTERVAL 秒，避免被限流（#56）。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import ClassVar

import pandas as pd

from quant.data.providers.base import DataProvider, DataUnavailableError, normalize_bars

URL = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK"
MIN_CALL_INTERVAL = 0.3  # 秒
_JSONP = re.compile(r"\((\[.*\])\)", re.DOTALL)

# 代码 → 响应文本
Fetch = Callable[[str], str]


def _http_get(symbol: str) -> str:
    url = f"{URL}?{urllib.parse.urlencode({'symbol': symbol})}"
    req = urllib.request.Request(
        url, headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # 固定的 https 地址
        return str(resp.read().decode("utf-8", errors="replace"))


def parse(text: str) -> pd.DataFrame:
    m = _JSONP.search(text)
    if not m:
        raise DataUnavailableError("新浪返回格式无法解析")
    items = json.loads(m.group(1))
    if not items:
        raise DataUnavailableError("新浪返回为空")
    df = pd.DataFrame(
        {
            "open": [float(i["o"]) for i in items],
            "high": [float(i["h"]) for i in items],
            "low": [float(i["l"]) for i in items],
            "close": [float(i["c"]) for i in items],
            "volume": [int(float(i["v"])) for i in items],
        },
        index=pd.DatetimeIndex([i["d"] for i in items]),
    )
    return df[~df.index.duplicated(keep="last")].sort_index()


class SinaProvider(DataProvider):
    source: ClassVar[str] = "sina"

    def __init__(
        self,
        fetch: Fetch = _http_get,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._retries = retries
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = MIN_CALL_INTERVAL - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if start > end:
            raise ValueError(f"start {start} 晚于 end {end}")
        self._throttle()
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                bars = normalize_bars(parse(self._fetch(symbol)), start, end)
            except Exception as e:  # 网络错误或格式异常：重试
                last = e
                self._sleep(2.0 * (attempt + 1))
                continue
            if bars.empty:
                raise DataUnavailableError(f"新浪没有 {symbol} {start}~{end} 的数据")
            return bars
        raise DataUnavailableError(f"新浪拉取 {symbol} 失败：{type(last).__name__}: {last}")
