"""老虎数据提供者：日线（PROJECT_PLAN 7.1）。tigeropen 类型只出现在本模块。

依据 Phase 0 报告 0.2：
- “前复权”（QuoteRight.BR）只调整拆股、不含分红，因此 open/high/low/close 与 yfinance 的
  Close 同口径，adj_close 置为 NaN；回测收益仍以 yfinance 的 adj_close 为准。
- 频率限制：K 线接口 60 次/分钟。分页请求间隔 2 秒，相邻两次调用之间至少间隔 1.1 秒。
- 历史行情额度：30 天内最多 20 个不同标的，只拉取确实需要的标的。
- 只读，不涉及下单。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import pandas as pd

from quant.core.settings import Settings
from quant.data.providers.base import DataProvider, DataUnavailableError, normalize_bars

ET = ZoneInfo("America/New_York")
MIN_CALL_INTERVAL = 1.1  # 秒，K 线 60 次/分钟
PAGE_INTERVAL = 2  # 秒，get_bars_by_page 分页之间
PAGE_SIZE = 1000  # 单次最多 1200 根
MAX_BARS = 20000


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp() * 1000)


def make_quote_client(settings: Settings) -> Any:
    """按配置创建 QuoteClient；配置文件必须存在且权限为 600。"""
    from tigeropen.quote.quote_client import QuoteClient
    from tigeropen.tiger_open_config import TigerOpenClientConfig

    props_dir = settings.tiger_props_file().parent
    return QuoteClient(TigerOpenClientConfig(props_path=str(props_dir) + "/"))


class TigerProvider(DataProvider):
    source: ClassVar[str] = "tiger"

    def __init__(
        self,
        client: Any,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> TigerProvider:
        return cls(make_quote_client(settings))

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = MIN_CALL_INTERVAL - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from tigeropen.common.consts import BarPeriod, QuoteRight

        if start > end:
            raise ValueError(f"start {start} 晚于 end {end}")
        self._throttle()
        try:
            raw = self._client.get_bars_by_page(
                symbol,
                period=BarPeriod.DAY,
                begin_time=_ms(start),
                end_time=_ms(end + timedelta(days=1)),  # end_time 不含
                total=MAX_BARS,
                page_size=PAGE_SIZE,
                right=QuoteRight.BR,
                time_interval=PAGE_INTERVAL,
            )
        except Exception as e:
            raise DataUnavailableError(
                f"老虎拉取 {symbol} 日线失败：{type(e).__name__}: {e}"
            ) from e
        if raw is None or len(raw) == 0:
            raise DataUnavailableError(f"老虎返回为空：{symbol} {start}~{end}")
        df = raw.copy()
        df.index = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(ET)
        return normalize_bars(df.drop(columns=["time"]), start, end)

    def kline_quota(self) -> dict[str, int]:
        """股票/ETF 历史行情额度：{"used": n, "remain": m}。"""
        self._throttle()
        for item in self._client.get_kline_quota(with_details=False):
            if item.get("method") == "kline":
                return {"used": int(item["used"]), "remain": int(item["remain"])}
        raise DataUnavailableError("未找到 kline 额度信息")
