"""老虎数据提供者：日线（PROJECT_PLAN 7.1）。tigeropen 类型只出现在本模块。

依据 Phase 0 报告 0.2：
- “前复权”（QuoteRight.BR）只调整拆股、不含分红，因此 open/high/low/close 与 yfinance 的
  Close 同口径。
- adjust_dividends=False 时 adj_close 为 NaN；为 True 时用老虎分红记录（get_corporate_dividend）
  按 yfinance 口径计算含分红复权价：除息日之前的价格乘以 (1 − 分红 / 除息前一日收盘)。
  服务器上不能用 yfinance（#41），每日任务以此为主数据源。实测 2023 年以来与 yfinance 一致；
  更早的分红记录有缺失，长期回测仍用 yfinance。
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
MAX_DIVIDEND_RATIO = 0.1  # 单次分红超过除息前收盘价的 10% 视为数据异常
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
        adjust_dividends: bool = False,
    ) -> None:
        self._client = client
        self._adjust = adjust_dividends
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    @classmethod
    def from_settings(cls, settings: Settings, adjust_dividends: bool = False) -> TigerProvider:
        return cls(make_quote_client(settings), adjust_dividends=adjust_dividends)

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
        bars = normalize_bars(df.drop(columns=["time"]), start, end)
        if self._adjust and len(bars):
            bars["adj_close"] = dividend_adjusted(bars["close"], self.dividends(symbol, start, end))
        return bars

    def dividends(self, symbol: str, start: date, end: date) -> list[tuple[date, float]]:
        """[start, end] 内的现金分红：[(除息日, 每股金额)]，已去重、按日期排序。"""
        from tigeropen.common.consts import Market

        self._throttle()
        try:
            raw = self._client.get_corporate_dividend(
                [symbol], Market.US, start.isoformat(), end.isoformat()
            )
        except Exception as e:
            raise DataUnavailableError(
                f"老虎拉取 {symbol} 分红失败：{type(e).__name__}: {e}"
            ) from e
        if raw is None or len(raw) == 0:
            return []
        rows = raw[raw["symbol"] == symbol] if "symbol" in raw.columns else raw
        out: set[tuple[date, float]] = set()
        for _, r in rows.iterrows():
            if str(r.get("currency") or "USD") != "USD":
                raise DataUnavailableError(f"{symbol} 分红币种为 {r['currency']}，不支持")
            out.add((pd.Timestamp(str(r["execute_date"])[:10]).date(), float(r["amount"])))
        return sorted(out)

    def kline_quota(self) -> dict[str, int]:
        """股票/ETF 历史行情额度：{"used": n, "remain": m}。"""
        self._throttle()
        for item in self._client.get_kline_quota(with_details=False):
            if item.get("method") == "kline":
                return {"used": int(item["used"]), "remain": int(item["remain"])}
        raise DataUnavailableError("未找到 kline 额度信息")


def dividend_adjusted(close: pd.Series, dividends: list[tuple[date, float]]) -> pd.Series:
    """按 yfinance 口径计算含分红复权收盘价（最新一天等于收盘价）。

    窗口第一天及之前的除息不影响窗口内的相对收益，忽略；晚于最后一天的同样忽略。
    """
    factor = pd.Series(1.0, index=close.index)
    days = pd.DatetimeIndex(close.index)
    for ex, amount in dividends:
        pos = int(days.searchsorted(pd.Timestamp(ex)))
        if pos == 0 or pos >= len(days):
            continue
        prev = float(close.iloc[pos - 1])
        ratio = amount / prev
        if not 0 < ratio < MAX_DIVIDEND_RATIO:
            raise DataUnavailableError(f"{ex} 分红 {amount} 相对收盘价 {prev} 异常（{ratio:.2%}）")
        factor.iloc[:pos] *= 1 - ratio
    return (close * factor).astype("float64")
