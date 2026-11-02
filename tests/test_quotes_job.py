"""盘中行情采集（#93）：只在常规时段，Finnhub，与交易链路隔离。"""

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.data.market_store import ETF_SET, MarketStore, gics_path, intraday_snapshot_path
from quant.data.providers.base import DataUnavailableError
from quant.data.providers.finnhub import Quote
from quant.db.models import IntradayQuote, PositionSnapshot
from quant.jobs import quotes

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # 美东 10:00，常规时段


class FakeFinnhub:
    def __init__(self, missing: set[str] = frozenset(), limit_at: int | None = None) -> None:  # type: ignore[assignment]
        self.missing, self.limit_at = missing, limit_at
        self.calls: list[str] = []

    def quote(self, symbol: str) -> Quote | None:
        self.calls.append(symbol)
        if self.limit_at is not None and len(self.calls) > self.limit_at:
            raise DataUnavailableError("Finnhub 返回 429")
        if symbol in self.missing:
            return None
        px = 100.0 + len(self.calls)
        return Quote(px, 99.0, 0.01, NOW - timedelta(seconds=30), open=99.5, high=px + 1, low=99.0)


def test_symbols_order_and_cap() -> None:
    from quant.data.market_store import ETF_SET

    members = [f"M{i:03d}" for i in range(800)]
    out = quotes.pick_symbols({"HELD", "QQQ"}, ("aapl", "M001"), members)
    assert out[: len(ETF_SET)] == list(ETF_SET)  # ETF 先（行情条、板块、风格）
    assert out[len(ETF_SET)] == "HELD"  # 再持仓
    assert len(out) == quotes.MAX_SYMBOLS and len(set(out)) == len(out)
    assert quotes.pick_symbols(set(), ("aapl",), ["M1"])[-2:] == ["M1", "AAPL"]


def test_isolated_from_trading() -> None:
    banned = "('tigeropen', 'quant.broker', 'quant.data.providers.tiger', 'quant.engine')"
    code = f"import sys, quant.jobs.quotes; print(any(m.startswith({banned}) for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@needs_db
def test_poll_writes_latest_and_skips_missing(db_url: str, tmp_path: Path) -> None:
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add(
            PositionSnapshot(
                snapshot_date=date(2026, 10, 30),
                mode="paper",
                symbol="MSFT",
                qty=1,
                avg_cost=Decimal(1),
                market_value=Decimal(1),
            )
        )
    fh = FakeFinnhub(missing={"DIA"})
    n = quotes.poll_once(eng, fh, (), NOW, tmp_path)
    etfs = list(ETF_SET)
    assert fh.calls == [*etfs, "MSFT"] and n == len(etfs)  # DIA 没有报价
    quotes.poll_once(eng, fh, (), NOW + timedelta(minutes=1), tmp_path)  # 再来一轮：覆盖，不新增
    with Session(eng) as s:
        rows = {q.symbol: q for q in s.scalars(select(IntradayQuote))}
    assert set(rows) == (set(ETF_SET) - {"DIA"}) | {"MSFT"}
    assert rows["SPY"].fetched_at == NOW + timedelta(minutes=1) and rows["SPY"].prev_close == 99.0
    eng.dispose()


@needs_db
def test_rate_limit_stops_round_without_raising(db_url: str, tmp_path: Path) -> None:
    eng = create_engine(db_url)
    fh = FakeFinnhub(limit_at=2)
    assert quotes.poll_once(eng, fh, (), NOW, tmp_path) == 2
    assert len(fh.calls) == 3  # 第 3 次被限流后本轮停止
    eng.dispose()


def test_runs_only_in_regular_session() -> None:
    from quant.core.calendar import get_calendar

    cal = get_calendar()
    assert quotes.should_poll(cal, NOW) is True
    assert (
        quotes.should_poll(cal, datetime(2026, 11, 2, 12, 0, tzinfo=UTC)) is False
    )  # 美东 07:00 盘前
    assert quotes.should_poll(cal, datetime(2026, 11, 7, 15, 0, tzinfo=UTC)) is False  # 周六


def _market(data_dir: Path) -> None:
    """5 个交易日的日线（截至 10-30）+ 2 只成分股。"""
    gp = gics_path(data_dir)
    gp.parent.mkdir(parents=True, exist_ok=True)
    gp.write_text(
        "Symbol,Security,GICS Sector\n"
        "AAPL,Apple,Information Technology\n"
        "MSFT,Microsoft,Information Technology\n",
        encoding="utf-8",
    )
    store = MarketStore(data_dir)
    syms = [*ETF_SET, "AAPL", "MSFT"]
    for i, d in enumerate([date(2026, 10, 26) + timedelta(days=k) for k in range(5)]):
        px = [100.0 + i] * len(syms)
        df = pd.DataFrame(
            {
                "symbol": syms,
                "open": px,
                "high": px,
                "low": px,
                "close": px,
                "volume": [1000.0] * len(syms),
                "vwap": px,
            }
        )
        store.write_day(d, df, keep=set(syms))


@needs_db
def test_round_writes_intraday_snapshot(db_url: str, tmp_path: Path) -> None:
    _market(tmp_path)
    eng = create_engine(db_url)
    fh = FakeFinnhub()
    quotes.poll_once(eng, fh, (), NOW, tmp_path)
    assert "AAPL" in fh.calls and "MSFT" in fh.calls  # 全市场：成分股也取
    doc = json.loads(intraday_snapshot_path(tmp_path).read_text(encoding="utf-8"))
    assert doc["intraday"] is True and doc["as_of"] == "2026-11-02"
    assert doc["intraday_time"].startswith("2026-11-02T14:59:30")
    spy = next(t for t in doc["strip"] if t["symbol"] == "SPY")
    assert spy["close"] == fh_price(fh, "SPY")
    eng.dispose()


def fh_price(fh: FakeFinnhub, sym: str) -> float:
    return 100.0 + fh.calls.index(sym) + 1


@needs_db
def test_stop_requested_ends_round_early(db_url: str, tmp_path: Path) -> None:
    eng = create_engine(db_url)
    fh = FakeFinnhub()
    quotes.poll_once(eng, fh, (), NOW, tmp_path, stop=lambda: len(fh.calls) >= 3)
    assert len(fh.calls) == 3
    eng.dispose()
