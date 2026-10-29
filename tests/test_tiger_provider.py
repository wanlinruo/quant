from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from quant.data.providers.base import BAR_COLUMNS, DataUnavailableError
from quant.data.providers.tiger import TigerProvider

ET = ZoneInfo("America/New_York")


def tiger_frame(days: list[date]) -> pd.DataFrame:
    """模拟 get_bars_by_page 的输出：time 为美东 0 点对应的毫秒时间戳。"""
    ms = [int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp() * 1000) for d in days]
    n = len(days)
    close = np.linspace(500, 500 + n - 1, n)
    return pd.DataFrame(
        {
            "symbol": "SPY",
            "time": ms,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1000, dtype="int64"),
            "amount": close * 1000,
        }
    )


class FakeClient:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_bars_by_page(self, symbol: str, **kw: Any) -> Any:
        self.calls.append((symbol, kw))
        if self.error:
            raise self.error
        return self.result

    def get_kline_quota(self, with_details: bool = False) -> list[dict[str, Any]]:
        return [
            {"remain": 10, "used": 0, "method": "future_kline"},
            {"remain": 15, "used": 5, "method": "kline"},
        ]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make(client: FakeClient) -> tuple[TigerProvider, FakeClock]:
    clock = FakeClock()
    return TigerProvider(client, clock=clock, sleep=clock.sleep), clock


def test_same_structure_as_yfinance() -> None:
    days = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
    p, _ = make(FakeClient(tiger_frame(days)))
    df = p.daily_bars("SPY", days[0], days[-1])
    assert tuple(df.columns) == BAR_COLUMNS
    assert df.index.name == "date"
    assert pd.DatetimeIndex(df.index).tz is None
    assert [d.date() for d in df.index] == days
    assert df["adj_close"].isna().all()  # 老虎前复权不含分红，不提供 adj_close


def test_request_parameters() -> None:
    from tigeropen.common.consts import QuoteRight

    client = FakeClient(tiger_frame([date(2026, 10, 28)]))
    p, _ = make(client)
    p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))
    symbol, kw = client.calls[0]
    assert symbol == "SPY"
    assert kw["right"] == QuoteRight.BR
    assert kw["page_size"] <= 1200
    assert kw["time_interval"] >= 1
    # end_time 不含当天，需 +1 天
    assert kw["end_time"] - kw["begin_time"] == 24 * 3600 * 1000


def test_throttles_consecutive_calls() -> None:
    p, clock = make(FakeClient(tiger_frame([date(2026, 10, 28)])))
    p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))
    p.daily_bars("QQQ", date(2026, 10, 28), date(2026, 10, 28))
    assert clock.sleeps == [pytest.approx(1.1)]
    clock.now += 5
    p.daily_bars("TLT", date(2026, 10, 28), date(2026, 10, 28))
    assert len(clock.sleeps) == 1


def test_errors_wrapped() -> None:
    p, _ = make(FakeClient(error=RuntimeError("code=5 rate limit")))
    with pytest.raises(DataUnavailableError, match="rate limit"):
        p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))
    p, _ = make(FakeClient(result=pd.DataFrame()))
    with pytest.raises(DataUnavailableError, match="为空"):
        p.daily_bars("SPY", date(2026, 10, 28), date(2026, 10, 28))


def test_kline_quota() -> None:
    p, _ = make(FakeClient())
    assert p.kline_quota() == {"used": 5, "remain": 15}


# ---- 分红复权（#41）----

from quant.data.providers.tiger import dividend_adjusted  # noqa: E402

DAYS = [date(2026, 3, d) for d in (16, 17, 18, 19, 20, 23)]


def test_dividend_adjusted_matches_yfinance_formula() -> None:
    close = pd.Series([100.0, 102.0, 101.0, 100.0, 99.0, 100.0], index=pd.DatetimeIndex(DAYS))
    adj = dividend_adjusted(close, [(date(2026, 3, 20), 1.0)])  # 除息前一日收盘 100
    assert adj.iloc[-2:].tolist() == [99.0, 100.0]  # 除息日及之后不变
    assert np.allclose(adj.iloc[:4], close.iloc[:4] * 0.99)
    # 除息日的复权收益 = (99 + 1) / 100 − 1 = 0
    assert adj.iloc[4] / adj.iloc[3] == pytest.approx(1.0)


def test_dividend_on_non_trading_day_and_out_of_window() -> None:
    close = pd.Series(100.0, index=pd.DatetimeIndex(DAYS))
    # 3-21 周六除息 → 作用于 3-23；窗口首日及之外的忽略
    adj = dividend_adjusted(
        close, [(date(2026, 3, 21), 2.0), (date(2026, 3, 16), 5.0), (date(2026, 4, 1), 5.0)]
    )
    assert adj.iloc[:5].tolist() == [98.0] * 5 and adj.iloc[5] == 100.0


@pytest.mark.parametrize("amount", [0.0, -1.0, 15.0])
def test_abnormal_dividend_rejected(amount: float) -> None:
    close = pd.Series(100.0, index=pd.DatetimeIndex(DAYS))
    with pytest.raises(DataUnavailableError, match="异常"):
        dividend_adjusted(close, [(date(2026, 3, 18), amount)])


class DivClient(FakeClient):
    def __init__(self, result: Any, dividends: pd.DataFrame | None) -> None:
        super().__init__(result)
        self.div = dividends

    def get_corporate_dividend(self, symbols: list[str], market: Any, b: str, e: str) -> Any:
        self.calls.append(("dividend", {"symbols": symbols, "begin": b, "end": e}))
        return self.div


def test_daily_bars_with_dividends() -> None:
    div = pd.DataFrame(
        {"symbol": ["SPY", "SPY", "QQQ"], "execute_date": ["2026-03-20"] * 3,
         "amount": [1.0, 1.0, 9.0], "currency": ["USD"] * 3}
    )  # fmt: skip  # SPY 重复一条，QQQ 的不应计入
    client = DivClient(tiger_frame(DAYS), div)
    p = TigerProvider(client, clock=FakeClock(), sleep=lambda s: None, adjust_dividends=True)
    bars = p.daily_bars("SPY", DAYS[0], DAYS[-1])
    close = bars["close"]
    expected = close.copy()
    expected.iloc[:4] *= 1 - 1.0 / close.iloc[3]
    assert np.allclose(bars["adj_close"], expected)
    assert client.calls[-1] == (
        "dividend", {"symbols": ["SPY"], "begin": "2026-03-16", "end": "2026-03-23"}
    )  # fmt: skip


def test_daily_bars_no_dividends_and_default_off() -> None:
    client = DivClient(tiger_frame(DAYS), None)
    p = TigerProvider(client, clock=FakeClock(), sleep=lambda s: None, adjust_dividends=True)
    bars = p.daily_bars("GLD", DAYS[0], DAYS[-1])
    assert (bars["adj_close"] == bars["close"]).all()
    off = TigerProvider(DivClient(tiger_frame(DAYS), None), clock=FakeClock(), sleep=lambda s: None)
    assert off.daily_bars("GLD", DAYS[0], DAYS[-1])["adj_close"].isna().all()


def test_non_usd_dividend_rejected() -> None:
    div = pd.DataFrame({"symbol": ["SPY"], "execute_date": ["2026-03-20"], "amount": [1.0],
                        "currency": ["HKD"]})  # fmt: skip
    p = TigerProvider(DivClient(tiger_frame(DAYS), div), clock=FakeClock(), sleep=lambda s: None,
                      adjust_dividends=True)  # fmt: skip
    with pytest.raises(DataUnavailableError, match="币种"):
        p.daily_bars("SPY", DAYS[0], DAYS[-1])
