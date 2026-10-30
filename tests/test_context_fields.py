"""Context 的开高低收量与事件日历；Backtester 注入；策略级调仓阈值（swing_pullback Task 1）。"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd
import pytest

from quant.broker.sim import CostModel
from quant.core.calendar import TradingCalendar
from quant.data.providers.base import normalize_bars
from quant.data.store import ParquetStore
from quant.engine.backtester import Backtester, field_loader_from_bars
from quant.strategies.base import Context, LookAheadError, Strategy
from quant.strategies.events import EventCalendar

AS_OF = date(2024, 5, 10)


def _bars(days: pd.DatetimeIndex, close: float = 100.0, adj: float = 100.0) -> pd.DataFrame:
    n = len(days)
    return normalize_bars(
        pd.DataFrame(
            {
                "open": np.full(n, close),
                "high": np.full(n, close * 1.01),
                "low": np.full(n, close * 0.99),
                "close": np.full(n, close),
                "adj_close": np.full(n, adj),
                "volume": np.full(n, 1000, dtype="int64"),
            },
            index=days,
        )
    )


def _wide_loader(symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
    return pd.DataFrame()


def _field_loader(symbols: Sequence[str], field: str, start: date | None, end: date) -> Any:
    days = pd.date_range(AS_OF - timedelta(days=10), AS_OF + timedelta(days=5), freq="D")
    return pd.DataFrame({s: np.arange(len(days), dtype=float) for s in symbols}, index=days)


def test_field_truncated_to_as_of() -> None:
    ctx = Context(as_of=AS_OF, loader=_wide_loader, field_loader=_field_loader)
    df = ctx.field(["AAPL"], "high")
    assert df.index[-1].date() == AS_OF


def test_field_future_end_raises() -> None:
    ctx = Context(as_of=AS_OF, loader=_wide_loader, field_loader=_field_loader)
    with pytest.raises(LookAheadError):
        ctx.field(["AAPL"], "high", end=AS_OF + timedelta(days=1))


def test_field_without_loader_raises() -> None:
    ctx = Context(as_of=AS_OF, loader=_wide_loader)
    with pytest.raises(ValueError, match="开高低收"):
        ctx.field(["AAPL"], "close")


def test_field_loader_adjusted() -> None:
    days = pd.bdate_range("2024-05-01", "2024-05-10")
    load = field_loader_from_bars({"AAPL": _bars(days, close=100.0, adj=50.0)})
    opens = load(["AAPL"], "open", None, AS_OF)["AAPL"]
    assert np.allclose(opens, 50.0)
    assert (load(["AAPL"], "volume", None, AS_OF)["AAPL"] == 1000).all()


def test_split_continuity() -> None:
    days = pd.bdate_range("2024-06-03", "2024-06-14")
    before, after = days[:5], days[5:]
    # 2:1 拆股前，原始收盘 200、复权价 100；拆股后原始收盘 101
    bars = pd.concat([_bars(before, close=200.0, adj=100.0), _bars(after, close=101.0, adj=101.0)])
    load = field_loader_from_bars({"NVDA": bars})
    close = load(["NVDA"], "close", None, days[-1].date())["NVDA"]
    ratio = (close / close.shift(1)).dropna()
    assert ((ratio > 0.95) & (ratio < 1.05)).all()


def test_event_calendar() -> None:
    ev = EventCalendar(
        earnings={"AAPL": (date(2024, 5, 3), date(2024, 8, 2))}, fomc=(date(2024, 9, 18),)
    )
    assert ev.next_earnings("AAPL", date(2024, 5, 3)) == date(2024, 8, 2)
    assert ev.next_earnings("AAPL", date(2024, 5, 2)) == date(2024, 5, 3)
    assert ev.next_earnings("AAPL", date(2024, 8, 2)) is None
    assert ev.next_earnings("MSFT", date(2024, 5, 1)) is None
    assert ev.is_fomc(date(2024, 9, 18)) and not ev.is_fomc(date(2024, 9, 17))


class _Fixed(Strategy):
    name = "fixed_test"
    min_weight_diff: ClassVar[float | None] = 0.3
    seen: ClassVar[list[EventCalendar]] = []

    def symbols(self, params: Mapping[str, Any]) -> list[str]:
        return ["AAA"]

    def target_weights(self, ctx: Context) -> dict[str, float]:
        _Fixed.seen.append(ctx.events)
        assert ctx.field(["AAA"], "close").index[-1].date() == ctx.as_of
        return {"AAA": 0.5}


def test_backtester_passes_events_and_threshold() -> None:
    cal = TradingCalendar()
    days = pd.DatetimeIndex(cal.trading_days(date(2024, 1, 2), date(2024, 3, 28)))
    px = 100 * np.cumprod(np.full(len(days), 1.01))  # 持续上涨，权重会漂移
    bars = normalize_bars(
        pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "adj_close": px,
                      "volume": np.full(len(days), 1000, dtype="int64")}, index=days)
    )  # fmt: skip
    events = EventCalendar(fomc=(date(2024, 1, 31),))
    _Fixed.seen.clear()
    bt = Backtester(
        _Fixed(), {"AAA": bars}, cal, CostModel(Decimal(0), Decimal(0), Decimal(0)),
        benchmark=None, events=events,
    )  # fmt: skip
    result = bt.run(days[0].date(), days[-1].date())
    assert _Fixed.seen and all(e is events for e in _Fixed.seen)
    assert len(result.fills) == 1  # 进场偏离 0.49 > 0.3；之后漂移不超过 0.3，不再平衡


def test_store_prices_volume(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write_bars("SPY", _bars(pd.bdate_range("2024-05-01", "2024-05-10")), "test")
    vol = store.prices(["SPY"], field="volume")["SPY"]
    assert (vol == 1000).all()


def test_event_calendar_picklable() -> None:
    import pickle

    ev = EventCalendar(earnings={"AAPL": (date(2024, 5, 3),)}, fomc=(date(2024, 9, 18),))
    back = pickle.loads(pickle.dumps(ev))
    assert back.next_earnings("AAPL", date(2024, 5, 1)) == date(2024, 5, 3)
    assert back.is_fomc(date(2024, 9, 18))
