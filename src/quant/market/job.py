"""每日任务 market_data 的主体（#69）。

顺序：同步成分表 → 取当日全市场日线 → 补历史 → 刷新市值 → 生成快照。

Massive 免费档每分钟 5 次（客户端已限速），一次任务约 2 + history_per_run + caps_per_run 次请求。
限流或临时故障时停止剩余请求、保留已取数据；夜间 require_current 未达标时记失败并告警。
盘中追补可以先保留降级快照，等待下一轮；key 无效时始终抛出。
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
from quant.data.market_store import ETF_SET, CapInfo, MarketStore, snapshot_path, sync_gics
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import http_get_text
from quant.data.providers.massive import MassiveAuthError, Split, TickerDetails
from quant.data.quota import atomic_write_text
from quant.market.board import build_snapshot

log = logging.getLogger("quant.market_data")
MIN_COVERAGE = 0.95  # 当日成分覆盖率低于此值时重取一次
SPLIT_DAYS_PER_RUN = 10  # 停跑多日后分几次追上，避免一次占满额度


class MarketDataPending(DataUnavailableError):
    """目标日市场数据尚未就绪；可保留快照，但不能把夜间任务记为成功。"""


class Client(Protocol):
    def grouped_daily(self, day: date) -> pd.DataFrame: ...
    def splits(self, day: date) -> list[Split]: ...
    def ticker_details(self, symbol: str) -> TickerDetails | None: ...


def run_market_data(
    client: Client,
    data_dir: Path,
    today: date,
    cal: TradingCalendar,
    settings: BoardSettings,
    holdings: set[str],
    fetch_text: Callable[[str], str] = http_get_text,
    now_day: date | None = None,
    *,
    require_current: bool = False,
) -> str:
    """now_day：按真实时间的最近一个已收盘交易日（补跑过去日期时晚于 today）。"""
    gics = sync_gics(data_dir, today, fetch_text)
    # 取到的数据都已按“取数当时”的拆股调整，所以拆股要先核对到真实的最近交易日，再取数
    upto = max(today, now_day or today)
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
            notes.append(f"数据源请求停止（{type(e).__name__}: {e}）")
            return False
        if df.empty:
            if day != today:
                store.mark_empty(
                    day,
                    source="massive.grouped_daily",
                    observed_on=today,
                )
                notes.append(f"{day} 数据源返回空，标记至 {today + timedelta(days=7)} 后复核")
            return False
        old_coverage = store.coverage(day, members) if store.has_day(day) else 0.0
        symbols = set(df["symbol"]) if "symbol" in df else set()
        new_coverage = len(symbols & set(members)) / len(members) if members else 0.0
        if old_coverage and new_coverage <= old_coverage:
            notes.append(
                f"{day} 重取覆盖 {new_coverage:.1%} 未优于旧文件 {old_coverage:.1%}，保留旧文件"
            )
            return False
        store.write_day(day, df, keep)
        if old_coverage:
            notes.append(f"{day} 覆盖由 {old_coverage:.1%} 提升至 {new_coverage:.1%}")
        return True

    def check_splits() -> bool:
        """核对到 upto 返回 True；中途限流 / 故障时置 stopped，进度保留到已核对的那天。"""
        nonlocal stopped
        last = store.splits_checked()
        days = [upto] if last is None else [d for d in cal.trading_days(last, upto) if d > last]
        for d in days[:SPLIT_DAYS_PER_RUN]:
            try:
                found = client.splits(d)
            except MassiveAuthError:
                raise
            except DataUnavailableError as e:
                stopped = True
                notes.append(f"拆股核对停在 {d}（{type(e).__name__}: {e}）")
                return False
            for sp in found:
                if sp.symbol in keep and store.apply_split(sp.symbol, sp.execution_date, sp.factor):
                    ratio = f"{sp.split_from:g}:{sp.split_to:g}"
                    notes.append(f"{sp.symbol} {sp.execution_date} 拆股 {ratio} 已换算")
            store.set_splits_checked(d)
        return (store.splits_checked() or date.min) >= upto

    # 拆股：核对上次之后每个交易日执行的拆股，换算之前已存的日线。没核对到 upto 之前，
    # 不取过去日期的数据（补历史、补跑日的重取），否则这些数据之后会被再换算一次。
    splits_done = check_splits()
    members = gics.members
    low = store.has_day(today) and store.coverage(today, members) < MIN_COVERAGE
    if (not store.has_day(today) or low) and not stopped and (today == upto or splits_done):
        fetch_day(today)
    coverage = store.coverage(today, members)
    stale = not store.has_day(today) or coverage < MIN_COVERAGE or not splits_done
    if stale:
        notes.append(f"{today} 日线未就绪（成分覆盖 {coverage:.1%}，拆股核对 {splits_done}）")
        if require_current:
            stopped = True  # 重试先取目标日，不把额度花在历史和市值上
    # 补历史：最近 history_days 个交易日中缺的，由近及远
    start = today - timedelta(days=int(settings.history_days * 1.6) + 10)
    window = cal.trading_days(start, today)[-settings.history_days :]
    empty = store.empty_days(today)
    missing = [d for d in reversed(window)
               if d != today and not store.has_day(d) and d not in empty]  # fmt: skip
    if empty:
        notes.append(f"空日冷却 {len(empty)} 天（到期后自动复核，写入成功可撤销）")
    fetched = 0
    for d in missing[: settings.history_per_run]:
        if stopped or not splits_done:
            break
        fetched += fetch_day(d)
    if missing and (stopped or not splits_done):
        notes.append("后续历史补齐未请求（数据源已停止或拆股尚未核对完成）")
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
                stopped = True
                break
            old = caps.get(s)
            cap = info.market_cap if info and info.market_cap else (old.market_cap if old else None)
            caps[s] = CapInfo(cap, info.name if info else (old.name if old else None), today)
            refreshed += 1
        store.write_caps(caps)
    elif settings.caps_per_run and stopped:
        notes.append("市值刷新未请求（本轮数据源已停止）")
    snap = build_snapshot(store, gics, today, stale, holdings)
    atomic_write_text(snapshot_path(data_dir), json.dumps(snap.to_json(), ensure_ascii=False,
                                                          allow_nan=False))  # fmt: skip
    have = len(store.days())
    summary = (f"快照截至 {snap.as_of}；历史 {have}/{settings.history_days} 天"
               f"（本次补 {fetched}）；市值 {refreshed} 只")  # fmt: skip
    detail = "；".join([summary, *notes])
    if require_current and (stale or snap.as_of < today.isoformat()):
        raise MarketDataPending(f"目标日 {today} 未就绪；{detail}；保留展示缓存，等待重试")
    if require_current and stopped:
        raise MarketDataPending(f"目标日 {today} 已生成，但本轮存在局部失败；{detail}；等待重试")
    return detail
