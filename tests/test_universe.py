"""成分股快照（#47 Task 1）。"""

import pickle
from datetime import date
from decimal import Decimal

import pytest

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.data.universe import parse_sp500_history
from quant.engine.backtester import Backtester
from quant.strategies.base import Context, Strategy
from quant.strategies.universe import Constituents
from test_backtester import sample_bars

TEXT = 'date,tickers\n2015-01-02,"AAA,BBB,CCC"\n2015-03-02,"AAA,CCC,DDD"\n'


def test_members_uses_latest_snapshot_on_or_before() -> None:
    u = parse_sp500_history(TEXT)
    assert u.members(date(2015, 1, 2)) == {"AAA", "BBB", "CCC"}
    assert u.members(date(2015, 3, 1)) == {"AAA", "BBB", "CCC"}
    assert u.members(date(2015, 3, 2)) == {"AAA", "CCC", "DDD"}


def test_members_before_first_snapshot_raises() -> None:
    with pytest.raises(ValueError, match="成分股"):
        parse_sp500_history(TEXT).members(date(2014, 12, 31))


def test_all_symbols_includes_snapshot_in_effect_at_start() -> None:
    u = parse_sp500_history(TEXT)
    assert u.all_symbols(date(2015, 2, 1), date(2015, 12, 31)) == {"AAA", "BBB", "CCC", "DDD"}


def test_constituents_pickles() -> None:
    u = parse_sp500_history(TEXT)
    assert pickle.loads(pickle.dumps(u)).members(date(2015, 3, 2)) == u.members(date(2015, 3, 2))


class Recorder(Strategy):
    name = "recorder"

    def __init__(self) -> None:
        self.seen: list[Constituents | None] = []

    def target_weights(self, ctx: Context) -> dict[str, float]:
        self.seen.append(ctx.universe)
        return {}


def test_backtester_passes_universe_to_context() -> None:
    cal = get_calendar()
    u = parse_sp500_history(TEXT)
    strat = Recorder()
    bt = Backtester(
        strat,
        {"SPY": sample_bars(cal, 1, 0.0, 0.0)},
        cal,
        CostModel(Decimal("0.005"), Decimal("1"), Decimal("0.0005")),
        benchmark=None,
        universe=u,
    )
    bt.run(date(2024, 1, 2), date(2024, 1, 4))
    assert strat.seen and all(x is u for x in strat.seen)
