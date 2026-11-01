"""策略归因输出（#67 B 批 Task 1）：子仓权重、择时系数、平仓原因。权重必须与 run 完全一致。"""

from dataclasses import replace
from datetime import date

import pytest

from quant.strategies.base import Attribution
from quant.strategies.combo import Combo
from quant.strategies.events import EventCalendar
from quant.strategies.momentum_select import MomentumSelect
from quant.strategies.swing_pullback import simulate
from test_combo import _full_ctx
from test_momentum_select import CAL, ctx
from test_swing_pullback import IDX, P, d, make_inputs


def exits_on(
    inp: object, i: int, events: EventCalendar | None = None, params: object = P
) -> dict[str, str]:
    out: dict[str, str] = {}
    simulate(inp, params, events or EventCalendar(), CAL, d(i), exits=out)  # type: ignore[arg-type]
    return out


def test_swing_exit_reasons() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    inp.close.loc[IDX[12] : IDX[14], "AAA"] = 103.0
    inp.sma_exit.loc[IDX[15], "AAA"] = 102.0
    inp.close.loc[IDX[15], "AAA"] = 101.0
    assert exits_on(inp, 14) == {}
    assert exits_on(inp, 15) == {"AAA": f"跌破 {P.exit_ma} 日线（已获利）"}

    stop = make_inputs()
    stop.long_sig.loc[IDX[10], "AAA"] = True
    stop.close.loc[IDX[13], "AAA"] = 92.0
    assert exits_on(stop, 13) == {"AAA": f"止损 -{P.stop_pct:.0%}"}

    sup = make_inputs()
    sup.long_sig.loc[IDX[10], "AAA"] = True
    sup.close.loc[IDX[13], "AAA"] = 94.0
    sup.sma_support.loc[IDX[13], "AAA"] = 98.0
    assert exits_on(sup, 13) == {"AAA": "跌破支撑线"}

    mh = make_inputs()
    mh.long_sig.loc[IDX[10], "AAA"] = True
    assert exits_on(mh, 40) == {"AAA": f"持有满 {P.max_hold} 天"}

    er = make_inputs()
    er.long_sig.loc[IDX[10], "AAA"] = True
    assert exits_on(er, 18, EventCalendar(earnings={"AAA": (d(20),)})) == {"AAA": "财报前离场"}

    fo = make_inputs()
    fo.long_sig.loc[IDX[10], "AAA"] = True
    assert exits_on(fo, 15, EventCalendar(fomc=(d(16),)), replace(P, fomc_exit=True)) == {
        "AAA": "FOMC 前离场"
    }


def test_default_attribution_is_single_sleeve() -> None:
    c = ctx(date(2024, 6, 10), {})
    w, a = MomentumSelect(CAL).run_attributed(c)
    assert w == MomentumSelect(CAL).run(c) and w
    assert a == Attribution(sleeves={"main": w}, timing_factor=None, exits={}, default_exit={})


@pytest.mark.parametrize("day", [date(2024, 5, 31), date(2024, 6, 10), date(2024, 6, 28)])
def test_combo_attribution_matches_run(day: date) -> None:
    c, _ = _full_ctx(day)
    w, a = Combo(CAL).run_attributed(c)
    assert w == Combo(CAL).run(c)
    total: dict[str, float] = {}
    for sleeve in a.sleeves.values():
        for s, x in sleeve.items():
            total[s] = total.get(s, 0.0) + x
    assert set(a.sleeves) == {"momentum", "swing"}
    assert total.keys() == w.keys() and all(total[s] == pytest.approx(w[s]) for s in w)
    assert a.timing_factor in (1.0, 0.5)
    assert a.default_exit["momentum"].startswith("动量排名跌出前 ")
