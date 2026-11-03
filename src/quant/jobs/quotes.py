"""quotes：盘中行情采集（#93），只供面板查看；交易继续只用日线。

    python -m quant.jobs.quotes [--once] [--interval 60]

- 只在常规交易时段（XNYS，含半日市）轮询 Finnhub `/quote`，每轮间隔最少 60 秒。
  免费档只覆盖常规时段；全市场一轮约 10–15 分钟，共享请求节流，每轮最多 MAX_SYMBOLS 个。
- 标的：首页 ETF + 各账户最新持仓 + 标普 500 成分股 + quotes.watchlist
  （约 540 只，一轮约 10 分钟）。
  每只只保留最新一条；每轮结束用盘中价格拼当日 K 线，重算首页快照写入 snapshot_intraday.json。
- 每 50 只报价落库，ETF 与持仓优先；资讯约每 15 分钟在轮次之间刷新，实际约 15–25 分钟。
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
from quant.core.settings import BoardSettings, ConfigError, load_settings
from quant.data.market_store import (
    ETF_SET,
    GicsTable,
    MarketStore,
    intraday_snapshot_path,
    load_gics,
)
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.finnhub import FinnhubAuthError, FinnhubClient, Quote
from quant.data.providers.fred import FredClient
from quant.data.providers.massive import MassiveClient
from quant.data.quota import atomic_write_text
from quant.db.models import IntradayQuote, PositionSnapshot
from quant.market.board import build_snapshot
from quant.market.extras_job import run_market_extras
from quant.market.job import MIN_COVERAGE as DAILY_COVERAGE
from quant.market.job import Client as MarketClient
from quant.market.job import run_market_data
from quant.market.refresh_metrics import record_refresh
from quant.market.session import ET, us_session

log = logging.getLogger("quant.quotes")

MAX_SYMBOLS = 700
MIN_COVERAGE = 0.9  # 今天有报价的标的比例低于此值时不更新盘中快照
SOURCE = "finnhub"
AUTH_BACKOFF = timedelta(minutes=30)
EXTRAS_INTERVAL = timedelta(minutes=15)
RECOVERY_INTERVAL = timedelta(minutes=15)


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


def extras_due(
    cal: TradingCalendar,
    now: datetime,
    next_at: datetime | None,
    stopped: bool,
) -> bool:
    return not stopped and should_poll(cal, now) and (next_at is None or now >= next_at)


def recover_previous_daily(
    engine: Engine,
    client: MarketClient | None,
    data_dir: Path,
    now: datetime,
    cal: TradingCalendar,
    stop: Callable[[], bool] = lambda: False,
) -> bool:
    """夜间数据未发布时，在下一常规时段追补市场展示日线；不接触交易数据。

    复用标准市场数据任务的拆股核对、成分表、覆盖率与原子写入。
    只补最近缺失日，不补长历史或市值，避免占满盘中额度。
    """
    if stop() or not should_poll(cal, now):
        return False
    previous = cal.previous_trading_day(now.astimezone(ET).date())
    store = MarketStore(data_dir)
    gics = load_gics(data_dir)
    if (
        gics is not None
        and store.has_day(previous)
        and store.coverage(previous, gics.members) >= DAILY_COVERAGE
    ):
        return True
    if client is None:
        log.warning(
            "市场日线截至 %s，缺少 %s 且未配置 Massive key，首页暂不能盘中更新",
            store.days()[-1] if store.days() else None,
            previous,
        )
        return False
    with Session(engine) as s:
        held = _held(s)
    result = run_market_data(
        client,
        data_dir,
        previous,
        cal,
        BoardSettings(history_per_run=0, caps_per_run=0),
        held,
        now_day=previous,
    )
    log.info("盘中基准日线追补：%s", result)
    gics = load_gics(data_dir)
    return bool(
        gics
        and store.has_day(previous)
        and store.coverage(previous, gics.members) >= DAILY_COVERAGE
    )


def poll_once(
    engine: Engine,
    source: QuoteSource,
    watchlist: Sequence[str],
    now: datetime,
    data_dir: Path,
    stop: Callable[[], bool] = lambda: False,
    cache: dict[str, Quote] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """取一轮报价写入 intraday_quotes，并重算盘中首页快照；返回写入条数。

    限流或网络错误、或收到停止信号时本轮提前结束（已取到的照常写入）。
    """
    gics = load_gics(data_dir)
    with Session(engine) as s:
        held = _held(s)
    symbols = pick_symbols(held, watchlist, gics.members if gics else ())
    got: dict[str, Quote] = {}
    pending: dict[str, Quote] = {}
    fetched: dict[str, datetime] = {}
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
            pending[sym] = q
            fetched[sym] = clock() if clock else now
        # ETF 与持仓优先取到后及时落库，不等全市场十多分钟全部完成。
        if len(pending) >= 50:
            _save_quotes(engine, pending, fetched)
            pending.clear()
    _save_quotes(engine, pending, fetched)
    if gics is not None:
        merged = cache if cache is not None else {}
        merged.update(got)
        _write_snapshot(data_dir, gics, held, merged, clock() if clock else now, symbols)
    return len(got)


def _save_quotes(engine: Engine, got: dict[str, Quote], fetched: dict[str, datetime]) -> None:
    if not got:
        return
    with Session(engine) as s, s.begin():
        for sym, q in got.items():
            s.merge(
                IntradayQuote(
                    symbol=sym,
                    price=q.price,
                    prev_close=q.prev_close,
                    change_pct=q.change_pct,
                    quote_time=q.time,
                    fetched_at=fetched[sym],
                    source=SOURCE,
                )
            )


def refresh_extras(
    engine: Engine,
    client: FinnhubClient,
    fred: FredClient | None,
    data_dir: Path,
    now: datetime,
    cal: TradingCalendar,
) -> str:
    """在报价轮次之间执行，共用同一个 Finnhub 客户端的限速器，不访问交易接口。"""
    with Session(engine) as s:
        held = _held(s)
    return run_market_extras(
        data_dir=data_dir,
        now=now,
        today=now.astimezone(ET).date(),
        cal=cal,
        holdings=held,
        fred=fred,
        finnhub=client,
    )


def _write_snapshot(
    data_dir: Path,
    gics: GicsTable,
    held: set[str],
    quotes: dict[str, Quote],
    now: datetime,
    symbols: Sequence[str],
) -> None:
    """用今天（美东）的盘中报价拼当日 K 线，重算首页快照。

    不写的情况：覆盖不足 MIN_COVERAGE（避免用残缺数据覆盖完整快照）；已存日线不是上一交易日
    （否则“1 日”实为多日涨跌）；当日日线已入库。
    """
    today = now.astimezone(ET).date()
    for s in [
        s
        for s, q in quotes.items()
        if q.time.astimezone(ET).date() != today or now - q.time > timedelta(minutes=20)
    ]:
        del quotes[s]  # 只留今天的
    fresh = {s: quotes[s] for s in symbols if s in quotes}
    if not symbols or len(fresh) / len(symbols) < MIN_COVERAGE:
        log.info("盘中快照未更新：今天有报价的标的 %d / %d", len(fresh), len(symbols))
        return
    store = MarketStore(data_dir)
    days = store.days()
    if not days or days[-1] != get_calendar().previous_trading_day(today):
        log.warning("盘中快照未更新：已存日线截至 %s，不是上一交易日", days[-1] if days else None)
        return
    if store.coverage(days[-1], gics.members) < DAILY_COVERAGE:
        log.warning("盘中快照未更新：上一交易日成分股覆盖不足")
        return
    bar = pd.DataFrame(
        {
            "open": {s: q.open or q.price for s, q in fresh.items()},
            "high": {s: max(q.high or q.price, q.price) for s, q in fresh.items()},
            "low": {s: min(q.low or q.price, q.price) for s, q in fresh.items()},
            "close": {s: q.price for s, q in fresh.items()},
        }
    )
    snap = build_snapshot(store, gics, today, stale=False, held=held, today_bar=bar)
    if not snap.intraday:
        return
    doc = snap.to_json()
    doc["intraday_time"] = max(q.time for q in fresh.values()).isoformat()
    doc["intraday_time_start"] = min(q.time for q in fresh.values()).isoformat()
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
    cal = get_calendar()
    stop = _Stop()
    client = (
        FinnhubClient(
            key,
            rate_limit_path=settings.data_dir / ".finnhub-rate",
            stop=lambda: stop.requested or not should_poll(cal, datetime.now(UTC)),
        )
        if key is not None and key.get_secret_value().strip()
        else None
    )
    fred_key = settings.fred_api_key
    fred = FredClient(fred_key) if fred_key and fred_key.get_secret_value().strip() else None
    massive_key = settings.massive_api_key
    massive = (
        MassiveClient(
            massive_key,
            rate_limit_path=settings.data_dir / ".massive-rate",
            stop=lambda: stop.requested or not should_poll(cal, datetime.now(UTC)),
        )
        if massive_key and massive_key.get_secret_value().strip()
        else None
    )
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    paused_until: datetime | None = None
    cache: dict[str, Quote] = {}
    next_extras: datetime | None = None
    next_recovery: datetime | None = None
    log.info("quotes 启动：常规时段轮询，最小轮次间隔 %.0f 秒，全市场约 10–15 分钟", args.interval)
    while not stop.requested:
        started = time.monotonic()
        now = datetime.now(UTC)
        if (
            client is not None
            and should_poll(cal, now)
            and (paused_until is None or now >= paused_until)
        ):
            try:
                if extras_due(cal, now, next_recovery, stop.requested):
                    next_recovery = now + RECOVERY_INTERVAL
                    try:
                        recover_previous_daily(
                            engine,
                            massive,
                            settings.data_dir,
                            now,
                            cal,
                            stop=lambda: stop.requested,
                        )
                    except Exception:
                        log.exception("市场基准日线追补失败，继续采集报价，稍后再试")
                if stop.requested or not should_poll(cal, datetime.now(UTC)):
                    continue
                cpu_start = time.process_time()
                requests_before, limits_before = client.request_count, client.rate_limit_count
                n = poll_once(
                    engine,
                    client,
                    settings.quotes.watchlist,
                    now,
                    settings.data_dir,
                    stop=lambda: stop.requested,
                    cache=cache,
                    clock=lambda: datetime.now(UTC),
                )
                record_refresh(
                    settings.data_dir,
                    "quotes",
                    started,
                    cpu_start,
                    n,
                    client.request_count - requests_before,
                    client.rate_limit_count - limits_before,
                )
                log.info("盘中行情 %d 只", n)
                # 半日市收盘后和停止期间不启动额外请求；夜间任务仍由 scheduler 执行。
                finished = datetime.now(UTC)
                if extras_due(cal, finished, next_extras, stop.requested):
                    next_extras = finished + EXTRAS_INTERVAL
                    extras_start, extras_cpu = time.monotonic(), time.process_time()
                    requests_before, limits_before = client.request_count, client.rate_limit_count
                    try:
                        log.info(
                            "盘中资讯：%s",
                            refresh_extras(
                                engine,
                                client,
                                fred,
                                settings.data_dir,
                                finished,
                                cal,
                            ),
                        )
                    except Exception:
                        log.exception("盘中资讯刷新失败，沿用已有文件")
                    record_refresh(
                        settings.data_dir,
                        "extras",
                        extras_start,
                        extras_cpu,
                        0,
                        client.request_count - requests_before,
                        client.rate_limit_count - limits_before,
                    )
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
