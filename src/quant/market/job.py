"""每日任务 market_data 的主体（#69）。

顺序：同步成分表 → 取当日全市场日线 → 补历史 → 刷新市值 → 生成快照。

Massive 免费档每分钟 5 次（客户端已限速），一次任务约 2 + history_per_run + caps_per_run 次请求。
限流或临时故障时停止剩余请求、保留已取数据，照常生成快照；key 无效时抛出（任务失败告警）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd

from quant.core.calendar import TradingCalendar
from quant.core.settings import BoardSettings
from quant.data.market_store import ETF_SET, CapInfo, MarketStore, market_dir, sync_gics
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import http_get_text
from quant.data.providers.massive import MassiveAuthError, TickerDetails
from quant.data.quota import atomic_write_text
from quant.market.board import build_snapshot

log = logging.getLogger("quant.market_data")


class Client(Protocol):
    def grouped_daily(self, day: date) -> pd.DataFrame: ...
    def ticker_details(self, symbol: str) -> TickerDetails | None: ...


def snapshot_path(data_dir: Path) -> Path:
    return market_dir(data_dir) / "snapshot.json"


def run_market_data(
    client: Client,
    data_dir: Path,
    today: date,
    cal: TradingCalendar,
    settings: BoardSettings,
    holdings: set[str],
    fetch_text: Callable[[str], str] = http_get_text,
) -> str:
    gics = sync_gics(data_dir, today, fetch_text)
    keep = set(gics.members) | set(ETF_SET) | holdings
    store = MarketStore(data_dir)
    notes: list[str] = []
    stopped = False
    stale = False

    def fetch_day(day: date) -> bool:
        """取一天写入；返回是否拿到数据。限流 / 临时故障时置 stopped。"""
        nonlocal stopped
        try:
            df = client.grouped_daily(day)
        except MassiveAuthError:
            raise
        except DataUnavailableError as e:
            stopped = True
            notes.append(f"限流或故障，停止本轮（{type(e).__name__}）")
            return False
        if df.empty:
            return False
        store.write_day(day, df, keep)
        return True

    if not store.has_day(today):
        stale = not fetch_day(today) and not stopped
        if stale:
            notes.append(f"{today} 当日数据为空，沿用上一交易日")
    # 补历史：最近 history_days 个交易日中缺的，由近及远
    start = today - timedelta(days=int(settings.history_days * 1.6) + 10)
    window = cal.trading_days(start, today)[-settings.history_days :]
    missing = [d for d in reversed(window) if d != today and not store.has_day(d)]
    fetched = 0
    for d in missing[: settings.history_per_run]:
        if stopped:
            break
        fetched += fetch_day(d)
    # 市值：从未取过的优先，其次最旧的
    caps = store.caps()
    refreshed = 0
    if not stopped and settings.caps_per_run:
        max_age = settings.caps_max_age_days
        due = [s for s in gics.members
               if s not in caps or (today - caps[s].updated).days >= max_age]  # fmt: skip
        due.sort(key=lambda s: (s in caps, caps[s].updated if s in caps else date.min))
        for s in due[: settings.caps_per_run]:
            try:
                info = client.ticker_details(s)
            except MassiveAuthError:
                raise
            except DataUnavailableError as e:
                notes.append(f"限流或故障，市值停在 {refreshed} 只（{type(e).__name__}）")
                break
            old = caps.get(s)
            cap = info.market_cap if info and info.market_cap else (old.market_cap if old else None)
            caps[s] = CapInfo(cap, info.name if info else (old.name if old else None), today)
            refreshed += 1
        store.write_caps(caps)
    snap = build_snapshot(store, gics, today, stale)
    atomic_write_text(snapshot_path(data_dir), json.dumps(snap.to_json(), ensure_ascii=False,
                                                          allow_nan=False))  # fmt: skip
    have = len(store.days())
    summary = (f"快照截至 {snap.as_of}；历史 {have}/{settings.history_days} 天"
               f"（本次补 {fetched}）；市值 {refreshed} 只")  # fmt: skip
    return "；".join([summary, *notes])
