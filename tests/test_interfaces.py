from collections.abc import Sequence
from datetime import date

import pandas as pd
import pytest

from quant.broker.base import Broker
from quant.strategies.base import Context, LookAheadError, Strategy, validate_weights

AS_OF = date(2026, 10, 28)
DATES = pd.bdate_range("2026-10-20", "2026-11-06")  # 跨越 as_of 前后


def leaky_loader(symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
    """故意忽略 end、返回全部数据的数据源，模拟实现有缺陷的 loader。"""
    return pd.DataFrame({s: range(len(DATES)) for s in symbols}, index=DATES, dtype=float)


def date_index_loader(symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
    idx = [d.date() for d in DATES]
    return pd.DataFrame({s: 1.0 for s in symbols}, index=idx)


def _ctx(loader: object = leaky_loader, **params: object) -> Context:
    return Context(AS_OF, loader, params)  # type: ignore[arg-type]


class TestNoLookAhead:
    def test_bars_truncated_even_if_loader_leaks(self) -> None:
        df = _ctx().bars(["SPY"])
        assert df.index.max() == pd.Timestamp(AS_OF)
        assert (df.index <= pd.Timestamp(AS_OF)).all()

    def test_request_after_as_of_raises(self) -> None:
        with pytest.raises(LookAheadError):
            _ctx().bars(["SPY"], end=date(2026, 10, 29))

    def test_bypassing_bars_via_loader_still_truncated(self) -> None:
        ctx = _ctx()
        df = ctx.loader(["SPY"], None, date(2030, 1, 1))
        assert df.index.max() == pd.Timestamp(AS_OF)

    def test_earlier_end_respected(self) -> None:
        df = _ctx().bars(["SPY"], end=date(2026, 10, 23))
        assert df.index.max() == pd.Timestamp("2026-10-23")

    def test_date_object_index(self) -> None:
        df = _ctx(date_index_loader).bars(["SPY"])
        assert max(df.index) == AS_OF

    def test_returned_frame_is_a_copy(self) -> None:
        source = leaky_loader(["SPY"], None, AS_OF)
        ctx = Context(AS_OF, lambda s, a, b: source)
        got = ctx.bars(["SPY"])
        got.iloc[0, 0] = -1.0
        assert source.iloc[0, 0] == 0.0

    def test_tz_aware_index_rejected(self) -> None:
        def tz_loader(symbols: Sequence[str], start: date | None, end: date) -> pd.DataFrame:
            return leaky_loader(symbols, start, end).tz_localize("UTC")

        with pytest.raises(ValueError, match="时区"):
            _ctx(tz_loader).bars(["SPY"])

    def test_strategy_cannot_see_future(self) -> None:
        seen: list[pd.Timestamp] = []

        class Peeker(Strategy):
            name = "peeker"

            def target_weights(self, ctx: Context) -> dict[str, float]:
                seen.append(ctx.bars(["SPY"]).index.max())
                return {"SPY": 1.0}

        assert Peeker().run(_ctx()) == {"SPY": 1.0}
        assert seen == [pd.Timestamp(AS_OF)]


def test_params_read_only() -> None:
    ctx = _ctx(lookback=126)
    assert ctx.params["lookback"] == 126
    with pytest.raises(TypeError):
        ctx.params["lookback"] = 1  # type: ignore[index]


class TestWeights:
    def test_valid(self) -> None:
        assert validate_weights({"QQQ": 0.6, "TLT": 0.4, "SPY": 0.0}) == {"QQQ": 0.6, "TLT": 0.4}
        assert validate_weights({}) == {}  # 全部现金

    def test_float_rounding_tolerated(self) -> None:
        validate_weights({"A": 0.1, "B": 0.2, "C": 0.7})

    @pytest.mark.parametrize(
        "w",
        [{"SPY": 1.01}, {"SPY": 0.6, "QQQ": 0.5}, {"SPY": -0.1}, {"SPY": float("nan")}],
    )
    def test_invalid(self, w: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            validate_weights(w)

    def test_run_validates(self) -> None:
        class Levered(Strategy):
            name = "levered"

            def target_weights(self, ctx: Context) -> dict[str, float]:
                return {"SPY": 2.0}

        with pytest.raises(ValueError, match="超过 1"):
            Levered().run(_ctx())


def test_broker_is_abstract() -> None:
    with pytest.raises(TypeError):
        Broker()  # type: ignore[abstract]
    assert len(Broker.__abstractmethods__) == 7
