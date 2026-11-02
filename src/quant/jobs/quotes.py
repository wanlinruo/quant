"""quotes：盘中行情采集（#93），只供面板查看；交易继续只用日线。

    python -m quant.jobs.quotes [--once] [--interval 60]

- 只在常规交易时段（XNYS，含半日市）运行，每 60 秒取一轮 Finnhub `/quote`
  （免费档只覆盖常规时段；60 次/分钟，每轮最多 MAX_SYMBOLS 个）。
- 标的：首页 ETF + 各账户最新持仓 + 标普 500 成分股 + quotes.watchlist
  （约 540 只，一轮约 10 分钟）。
  每只只保留最新一条；每轮结束用盘中价格拼当日 K 线，重算首页快照写入 snapshot_intraday.json。
- 与交易链路隔离：不加载老虎配置，不导入 tigeropen / broker / 执行器。
- 失败只记日志（限流时本轮停止），不退出、不发交易告警。
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Protocol

import pandas as pd
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

from quant.core.calendar import TradingCalendar, get_calendar
from quant.core.settings import ConfigError, load_settings
from quant.data.market_store import (
    ETF_SET,
    GicsTable,
    MarketStore,
    intraday_snapshot_path,
    load_gics,
)
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.finnhub import FinnhubAuthError, FinnhubClient, Quote
from quant.data.quota import atomic_write_text
from quant.db.models import IntradayQuote, PositionSnapshot
from quant.market.board import build_snapshot
from quant.market.session import ET, us_session

log = logging.getLogger("quant.quotes")

MAX_SYMBOLS = 700
SOURCE = "finnhub"
AUTH_BACKOFF = timedelta(minutes=30)


class QuoteSource(Protocol):
    def quote(self, symbol: str) -> Quote | None: ...


def pick_symbols(
    held: Iterable[str], watchlist: Sequence[str], members: Iterable[str] = ()
) -> list[str]:
    """首页 ETF → 持仓 → 标普 500 成分股 → 关注列表，去重后最多 MAX_SYMBOLS 个。"""
    out = [
        *ETF_SET,
        *sorted(set(held)),
        *sorted(set(members)),
        *(w.strip().upper() for w in watchlist),
    ]
    return list(dict.fromkeys(s for s in out if s))[:MAX_SYMBOLS]


def _held(s: Session) -> set[str]:
    latest = (
        select(PositionSnapshot.mode, func.max(PositionSnapshot.snapshot_date).label("d"))
        .group_by(PositionSnapshot.mode)
        .subquery()
    )
    q = select(PositionSnapshot.symbol).join(
        latest,
        (PositionSnapshot.mode == latest.c.mode) & (PositionSnapshot.snapshot_date == latest.c.d),
    )
    return set(s.scalars(q))


def should_poll(cal: TradingCalendar, now: datetime) -> bool:
    return us_session(cal, now) == "regular"


def poll_once(
    engine: Engine,
    source: QuoteSource,
    watchlist: Sequence[str],
    now: datetime,
    data_dir: Path,
    stop: Callable[[], bool] = lambda: False,
) -> int:
    """取一轮报价写入 intraday_quotes，并重算盘中首页快照；返回写入条数。

    限流或网络错误、或收到停止信号时本轮提前结束（已取到的照常写入）。
    """
    gics = load_gics(data_dir)
    with Session(engine) as s:
        held = _held(s)
    symbols = pick_symbols(held, watchlist, gics.members if gics else ())
    got: dict[str, Quote] = {}
    for sym in symbols:
        if stop():
            break
        try:
            q = source.quote(sym)
        except FinnhubAuthError:
            raise
        except DataUnavailableError as e:
            log.warning("盘中行情本轮停止于 %s：%s", sym, e)
            break
        if q is not None:
            got[sym] = q
    with Session(engine) as s, s.begin():
        for sym, q in got.items():
            s.merge(
                IntradayQuote(
                    symbol=sym,
                    price=q.price,
                    prev_close=q.prev_close,
                    change_pct=q.change_pct,
                    quote_time=q.time,
                    fetched_at=now,
                    source=SOURCE,
                )
            )
    if gics is not None:
        _write_snapshot(data_dir, gics, held, got, now)
    return len(got)


def _write_snapshot(
    data_dir: Path, gics: GicsTable, held: set[str], got: dict[str, Quote], now: datetime
) -> None:
    """用今天（美东）的盘中报价拼当日 K 线，重算首页快照；当日已有日线时不写。"""
    today = now.astimezone(ET).date()
    fresh = {s: q for s, q in got.items() if q.time.astimezone(ET).date() == today}
    if not fresh:
        return
    bar = pd.DataFrame(
        {
            "open": {s: q.open or q.price for s, q in fresh.items()},
            "high": {s: max(q.high or q.price, q.price) for s, q in fresh.items()},
            "low": {s: min(q.low or q.price, q.price) for s, q in fresh.items()},
            "close": {s: q.price for s, q in fresh.items()},
        }
    )
    snap = build_snapshot(MarketStore(data_dir), gics, today, stale=False, held=held, today_bar=bar)
    if not snap.intraday:
        return
    doc = snap.to_json()
    doc["intraday_time"] = max(q.time for q in fresh.values()).isoformat()
    text = json.dumps(doc, ensure_ascii=False, allow_nan=False)
    atomic_write_text(intraday_snapshot_path(data_dir), text)


class _Stop:
    requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        self.requested = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quotes")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=60.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as e:
        log.error("配置错误：%s", e)
        return 1
    if settings.database_url is None:
        log.error("未配置 QUANT_DATABASE_URL")
        return 1
    key = settings.finnhub_api_key
    if key is None or not key.get_secret_value().strip():
        log.warning("未配置 QUANT_FINNHUB_API_KEY，盘中行情不采集")
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    client = FinnhubClient(key) if key is not None and key.get_secret_value().strip() else None
    cal = get_calendar()
    stop = _Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    paused_until: datetime | None = None
    log.info("quotes 启动：常规时段每 %.0f 秒采集一次", args.interval)
    while not stop.requested:
        started = time.monotonic()
        now = datetime.now(UTC)
        if (
            client is not None
            and should_poll(cal, now)
            and (paused_until is None or now >= paused_until)
        ):
            try:
                n = poll_once(
                    engine,
                    client,
                    settings.quotes.watchlist,
                    now,
                    settings.data_dir,
                    stop=lambda: stop.requested,
                )
                log.info("盘中行情 %d 只", n)
            except FinnhubAuthError as e:
                log.error("%s；%d 分钟后再试", e, AUTH_BACKOFF.seconds // 60)
                paused_until = now + AUTH_BACKOFF
            except Exception:  # 采集失败不退出
                log.exception("盘中行情采集失败")
        if args.once:
            break
        end = started + args.interval  # 一轮超过间隔（全市场约 10 分钟）时不再额外等待
        while not stop.requested and time.monotonic() < end:
            time.sleep(1)
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
