"""盘中行情采集（#93）：只在常规时段，Finnhub，与交易链路隔离。"""

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api_fixtures import needs_db
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
        return Quote(100.0 + len(self.calls), 99.0, 0.01, NOW - timedelta(seconds=30))


def test_symbols_order_and_cap() -> None:
    held = {f"H{i:02d}" for i in range(60)} | {"QQQ"}
    out = quotes.pick_symbols(held, watchlist=("AAPL", "SPY"))
    assert out[:4] == ["SPY", "QQQ", "DIA", "IWM"]
    assert len(out) == quotes.MAX_SYMBOLS and "AAPL" not in out  # 持仓优先于关注列表
    assert quotes.pick_symbols({"MSFT"}, ("aapl", "MSFT")) == [
        "SPY",
        "QQQ",
        "DIA",
        "IWM",
        "MSFT",
        "AAPL",
    ]


def test_isolated_from_trading() -> None:
    banned = "('tigeropen', 'quant.broker', 'quant.data.providers.tiger', 'quant.engine')"
    code = f"import sys, quant.jobs.quotes; print(any(m.startswith({banned}) for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@needs_db
def test_poll_writes_latest_and_skips_missing(db_url: str) -> None:
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
    n = quotes.poll_once(eng, fh, (), NOW)
    assert n == 4 and fh.calls == ["SPY", "QQQ", "DIA", "IWM", "MSFT"]
    n = quotes.poll_once(eng, fh, (), NOW + timedelta(minutes=1))  # 再来一轮：覆盖，不新增
    with Session(eng) as s:
        rows = {q.symbol: q for q in s.scalars(select(IntradayQuote))}
    assert set(rows) == {"SPY", "QQQ", "IWM", "MSFT"}
    assert rows["SPY"].fetched_at == NOW + timedelta(minutes=1) and rows["SPY"].prev_close == 99.0
    eng.dispose()


@needs_db
def test_rate_limit_stops_round_without_raising(db_url: str) -> None:
    eng = create_engine(db_url)
    fh = FakeFinnhub(limit_at=2)
    assert quotes.poll_once(eng, fh, (), NOW) == 2
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
