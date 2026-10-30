"""财报反应日：SEC EDGAR 8-K Item 2.02（Results of Operations），swing_pullback 设计第 6 节。

- 数据：https://data.sec.gov/submissions/CIK##########.json 及其 filings.files 中的历史分页。
- SEC 要求请求声明联系方式（User-Agent 含名称与邮箱），由调用方传入，不写在代码里；
  访问频率上限 10 次/秒，这里每次请求间隔 ≥ 0.15 秒。
- acceptanceDateTime 为 UTC（2026-10-29 用 AAPL 2024-05-02 16:30 EDT 发布的财报核实）。
- 反应日：受理时间（美东）早于当日收盘的交易日 → 当日；收盘后或非交易日 → 下一交易日。
- TSLA 的季度交付数据也以 8-K Item 2.02 申报（每季度初），一并视为财报类事件，持仓同样提前离场。
- 本机代理访问 data.sec.gov 不稳定时，可设 NO_PROXY=data.sec.gov 直连。
- 只用于 Mac 上的研究回测（历史日期）；未来的财报日期需另取（老虎财报日历）。
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from quant.core.calendar import TradingCalendar, get_calendar

ET = ZoneInfo("America/New_York")
BASE = "https://data.sec.gov/submissions/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
START = date(2014, 1, 1)
MIN_INTERVAL = 0.15

# 2015-10 之前 Google 以 Google Inc.（1288776）申报，之后为 Alphabet（1652044）
CIKS: dict[str, tuple[int, ...]] = {
    "AAPL": (320193,),
    "MSFT": (789019,),
    "NVDA": (1045810,),
    "GOOGL": (1652044, 1288776),
    "AMZN": (1018724,),
    "META": (1326801,),
    "TSLA": (1318605,),
    "MU": (723125,),
}

Fetch = Callable[[str], Mapping[str, Any]]


def http_fetcher(user_agent: str) -> Fetch:
    def fetch(url: str) -> Mapping[str, Any]:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:  # 固定的 https 地址
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"EDGAR 返回格式异常：{url}")
        return data

    return fetch


def retrying(fetch: Fetch, retries: int = 6, sleep: Callable[[float], None] = time.sleep) -> Fetch:
    """网络错误时重试（间隔 2、4、8… 秒，SEC 握手偶发超时），全部失败抛出最后一次的错误。"""

    def wrapped(url: str) -> Mapping[str, Any]:
        for attempt in range(retries):
            try:
                return fetch(url)
            except OSError:
                if attempt == retries - 1:
                    raise
                sleep(2.0 * 2**attempt)
        raise AssertionError("unreachable")

    return wrapped


def _table(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """submissions 主文件的 filings.recent，或历史分页文件本身（同样的列式结构）。"""
    filings = doc.get("filings")
    return filings["recent"] if isinstance(filings, Mapping) else doc


def parse_earnings_filings(doc: Mapping[str, Any]) -> list[datetime]:
    """8-K 且 items 含 2.02 的受理时间（UTC）。"""
    t = _table(doc)
    out = []
    for form, accepted, items in zip(
        t.get("form", []), t.get("acceptanceDateTime", []), t.get("items", []), strict=True
    ):
        if form == "8-K" and "2.02" in str(items).split(","):
            out.append(datetime.fromisoformat(str(accepted).replace("Z", "+00:00")))
    return out


def reaction_day(accepted: datetime, cal: TradingCalendar) -> date:
    local = accepted.astimezone(ET)
    d = local.date()
    if cal.is_trading_day(d) and accepted < cal.session_close(d):
        return d
    return cal.next_trading_day(d)


def fetch_earnings(
    symbol: str,
    user_agent: str,
    fetch: Fetch | None = None,
    sleep: Callable[[float], None] = time.sleep,
    cal: TradingCalendar | None = None,
    ciks: Sequence[int] | None = None,
) -> list[date]:
    """symbol 自 2014 年起的财报反应日，升序、去重。ciks 为 None 时用 CIKS[symbol]。"""
    fetch = fetch or retrying(http_fetcher(user_agent), sleep=sleep)
    cal = cal or get_calendar()
    accepted: list[datetime] = []
    for cik in CIKS[symbol] if ciks is None else ciks:
        main = fetch(f"{BASE}CIK{cik:010d}.json")
        sleep(MIN_INTERVAL)
        accepted += parse_earnings_filings(main)
        for f in main.get("filings", {}).get("files", []):
            accepted += parse_earnings_filings(fetch(BASE + str(f["name"])))
            sleep(MIN_INTERVAL)
    days = {reaction_day(a, cal) for a in accepted}
    return sorted(d for d in days if d >= START)


def fetch_cik_map(user_agent: str, fetch: Fetch | None = None) -> dict[str, int]:
    """SEC 当前上市公司的代码 → CIK（代码中的 - 换成 .，与标普成分名单一致）。

    只含当前代码：已退市或改过代码的公司查不到；代码被重新使用时可能对应到别的公司。
    """
    fetch = fetch or retrying(http_fetcher(user_agent))
    doc = fetch(TICKERS_URL)
    return {str(v["ticker"]).replace("-", "."): int(v["cik_str"]) for v in doc.values()}
