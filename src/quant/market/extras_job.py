"""每日任务 market_extras 的主体（#69 第 2 批）：宏观、新闻、财经日历。

各区块互不影响：某个数据源暂时拿不到时保留该区块的旧文件、写进摘要；key 无效（认证错误）时
先把其他区块跑完、写好，最后再抛出（任务失败告警）。未配置 key 的数据源对应区块跳过。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from quant.core.calendar import TradingCalendar
from quant.data.events import load_fomc_dates
from quant.data.market_store import (
    MarketStore,
    calendar_path,
    ff_events_path,
    load_gics,
    macro_path,
    news_path,
    snapshot_path,
)
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.coinbase import btc_daily
from quant.data.providers.finnhub import EarningsRaw, FinnhubAuthError, NewsRaw
from quant.data.providers.forexfactory import this_week
from quant.data.providers.fred import FredAuthError, Obs
from quant.data.providers.http import Fetch, http_fetch
from quant.data.quota import atomic_write_text
from quant.market.calendar import ACTUALS, build_calendar, fred_actual, merge_ff
from quant.market.macro import MACRO_SERIES, build_macro
from quant.market.news import build_feed, pick_reason

log = logging.getLogger("quant.market_extras")
ET = ZoneInfo("America/New_York")
REASON_TOP = 30  # 为涨跌幅最大的前几只异动股找原因
BIG_CAP = 100e9  # 财报只收持仓与市值 ≥ 1000 亿美元的成分股
EARNINGS_AHEAD = 7
MACRO_LOOKBACK = 260
AUTH_ERRORS = (FinnhubAuthError, FredAuthError)


class FredLike(Protocol):
    def observations(self, series: str, start: date) -> list[Obs]: ...
    def last_updated(self, series: str) -> datetime: ...


class FinnhubLike(Protocol):
    def market_news(self) -> list[NewsRaw]: ...
    def company_news(self, symbol: str, start: date, end: date) -> list[NewsRaw]: ...
    def earnings(self, day: date) -> list[EarningsRaw]: ...


def _write(path: Path, doc: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, allow_nan=False))


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_market_extras(
    *,
    data_dir: Path,
    now: datetime,
    today: date,
    cal: TradingCalendar,
    holdings: set[str],
    fred: FredLike | None,
    finnhub: FinnhubLike | None,
    fetch: Fetch = http_fetch,
) -> str:
    notes: list[str] = []
    auth: list[DataUnavailableError] = []
    gen = now.astimezone(ET)
    stamp = gen.isoformat()
    prev_day = cal.previous_trading_day(today)

    def fail(what: str, e: DataUnavailableError) -> None:
        if isinstance(e, AUTH_ERRORS):
            auth.append(e)
        notes.append(f"{what}暂不可用（{e}）")

    # ---- 宏观 ----
    fred_obs: dict[str, list[Obs]] = {}
    fred_down = False
    if fred is None:
        notes.append("未配置 FRED key，宏观项与经济数据实际值跳过")
    else:
        for series, _, _ in MACRO_SERIES.values():
            try:
                fred_obs[series] = fred.observations(series, today - timedelta(days=MACRO_LOOKBACK))
            except DataUnavailableError as e:
                fail(f"FRED {series} ", e)
                fred_down = True  # 一次失败即停：FRED 无响应时每次请求要等 60 秒
                break
    try:
        btc = btc_daily(fetch, now=now)
    except DataUnavailableError as e:
        btc = None
        fail("Coinbase ", e)
    fresh = {i.symbol: asdict(i) for i in build_macro(fred_obs, btc)}
    # 本次没取到的项沿用上次的（每项带自己的 as_of，页面会注明“截至 MM-DD”）
    old_doc = _read(macro_path(data_dir))
    old_items = old_doc.get("items") if isinstance(old_doc, dict) else None
    old = {i["symbol"]: i for i in old_items or [] if isinstance(i, dict) and "symbol" in i}
    items = [fresh.get(k) or old[k] for k in (*MACRO_SERIES, "BTC") if k in fresh or k in old]
    if fresh:
        _write(macro_path(data_dir), {"generated_at": stamp, "items": items})
    summary = [f"宏观 {len(fresh)} 项"]

    # ---- 新闻与异动原因 ----
    snap = _read(snapshot_path(data_dir))
    as_of = (
        date.fromisoformat(snap["as_of"]) if isinstance(snap, dict) and snap.get("as_of") else None
    )
    movers = snap.get("movers") or [] if isinstance(snap, dict) else []
    if finnhub is None:
        notes.append("未配置 Finnhub key，新闻与财报跳过")
    else:
        try:
            market = finnhub.market_news()
            top = sorted(movers, key=lambda m: -abs(m.get("change_pct") or 0))[:REASON_TOP]
            gics = load_gics(data_dir)
            names = {s: gics.name(s) for s in holdings} if gics else {}
            names |= {m["symbol"]: m.get("name") for m in movers}
            symbols = list(dict.fromkeys([m["symbol"] for m in top] + sorted(holdings)))
            base = as_of or today
            since = datetime.combine(cal.previous_trading_day(base), time(16, 0), ET)
            company: dict[str, list[NewsRaw]] = {}
            for s in symbols:
                try:
                    company[s] = finnhub.company_news(
                        s, cal.previous_trading_day(base), today + timedelta(days=1)
                    )
                except DataUnavailableError as e:
                    if isinstance(e, AUTH_ERRORS):
                        raise
                    notes.append(f"公司新闻停在 {s}（{e}）")
                    break
            reasons = {}
            if as_of is not None:
                for sym in symbols:  # 前 30 只与持仓中出现在异动表里的
                    if sym not in names:
                        continue
                    r = pick_reason(sym, names[sym], company.get(sym, []), since)
                    if r is not None:
                        reasons[sym] = asdict(r)
            feed_since = datetime.combine(prev_day, time(0), ET)
            feed = build_feed(market, company, holdings, feed_since, names=names)
            _write(news_path(data_dir), {"generated_at": stamp,
                                         "as_of": as_of.isoformat() if as_of else None,
                                         "items": [asdict(i) for i in feed],
                                         "reasons": reasons})  # fmt: skip
            summary.append(f"新闻 {len(feed)} 条（原因 {len(reasons)} 只）")
        except DataUnavailableError as e:
            fail("Finnhub 新闻", e)

    # ---- 财经日历 ----
    stored = _read(ff_events_path(data_dir))
    ff = stored if isinstance(stored, list) else []
    try:
        ff = merge_ff(ff, this_week(fetch), today)
        atomic_write_text(ff_events_path(data_dir), json.dumps(ff, ensure_ascii=False))
    except DataUnavailableError as e:
        fail("Forex Factory ", e)
    actuals: dict[str, str | None] = {}
    if fred is not None and not fred_down:
        cache: dict[str, tuple[list[Obs], datetime]] = {}
        start = datetime.combine(prev_day, time(0), ET)
        for ev in ff:
            t = datetime.fromisoformat(ev["time"]).astimezone(ET)
            spec = ACTUALS.get(ev["title"])
            if spec is None or not (start <= t <= gen):
                continue
            series = spec[0]
            try:
                if series not in cache:
                    cache[series] = (fred.observations(series, t.date() - timedelta(days=400)),
                                     fred.last_updated(series))  # fmt: skip
            except DataUnavailableError as err:
                fail(f"FRED {series} ", err)
                break
            obs, upd = cache[series]
            actuals[f"{ev['title']}|{t.date().isoformat()}"] = fred_actual(
                ev["title"], t.date(), obs, upd
            )
    earnings: list[EarningsRaw] = []
    if finnhub is not None:
        days = [prev_day, today] + [today + timedelta(days=i) for i in range(1, EARNINGS_AHEAD + 1)]
        try:
            for d in days:
                earnings += finnhub.earnings(d)
        except DataUnavailableError as e:
            fail("Finnhub 财报", e)
    caps = MarketStore(data_dir).caps()
    big = {s for s, c in caps.items() if c.market_cap and c.market_cap >= BIG_CAP}
    released, upcoming = build_calendar(ff, actuals, earnings, load_fomc_dates(), holdings, big,
                                        gen, prev_day)  # fmt: skip
    _write(calendar_path(data_dir), {"generated_at": stamp,
                                     "released": [asdict(i) for i in released],
                                     "upcoming": [asdict(i) for i in upcoming]})  # fmt: skip
    summary.append(f"日历 已公布 {len(released)} / 接下来 {len(upcoming)}")

    text = "；".join([*summary, *notes])
    if auth:
        log.warning(text)
        raise auth[0]
    return text
