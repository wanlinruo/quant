"""swing_pullback 策略（Task 5）。simulate 用手工设定的信号测试状态机；另有端到端接线测试。"""

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar
from quant.data.providers.base import normalize_bars
from quant.engine.backtester import field_loader_from_bars
from quant.strategies import get_strategy
from quant.strategies.base import FIELDS, Context
from quant.strategies.events import EventCalendar
from quant.strategies.swing_pullback import (
    Inputs,
    SwingParams,
    SwingPullback,
    compute_inputs,
    simulate,
)

CAL = TradingCalendar()
DAYS = CAL.trading_days(date(2024, 1, 2), date(2024, 6, 28))
IDX = pd.DatetimeIndex(DAYS)
STOCKS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
SYMS = [*STOCKS, "QQQ", "PSQ"]
P = SwingParams.from_mapping({"stocks": list(STOCKS), "replay_days": 100})


def d(i: int) -> date:
    return DAYS[i]


def make_inputs() -> Inputs:
    def wide(v: float | bool) -> pd.DataFrame:
        return pd.DataFrame(v, index=IDX, columns=SYMS)

    return Inputs(
        open=wide(100.0), close=wide(100.0), sma_support=wide(95.0), sma_exit=wide(99.0),
        score=wide(0.0), vol=wide(0.02), long_sig=wide(False),
        short_sig=pd.Series(False, index=IDX),
    )  # fmt: skip


def held(
    inp: Inputs, i: int, events: EventCalendar | None = None, params: SwingParams = P
) -> dict[str, float]:
    return simulate(inp, params, events or EventCalendar(), CAL, d(i))


def test_entry_then_trend_exit() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    inp.close.loc[IDX[12] : IDX[14], "AAA"] = 103.0  # 浮盈 3% ≥ 2%：离场规则生效
    inp.sma_exit.loc[IDX[15], "AAA"] = 102.0
    inp.close.loc[IDX[15], "AAA"] = 101.0  # 跌破 5 日线
    w10 = held(inp, 10)
    assert set(w10) == {"AAA"}
    assert held(inp, 14) == w10  # 持有期间权重不变
    assert "AAA" not in held(inp, 15)


def test_trend_exit_requires_armed() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    inp.sma_exit.loc[IDX[13], "AAA"] = 101.0  # 收盘 100 < 101，但从未浮盈 2%
    assert "AAA" in held(inp, 13)


def test_stop_loss() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    inp.close.loc[IDX[13], "AAA"] = 92.0  # 进场价 100（次日开盘），跌 8% ≥ 7%
    assert "AAA" in held(inp, 12) and "AAA" not in held(inp, 13)


def test_support_break() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    inp.close.loc[IDX[13], "AAA"] = 94.0  # 跌 6%（未到止损），但低于 95 × 0.97 = 92.15？否
    assert "AAA" in held(inp, 13)
    inp.sma_support.loc[IDX[13], "AAA"] = 98.0  # 94 < 98 × 0.97 = 95.06
    assert "AAA" not in held(inp, 13)


def test_max_hold() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True  # 第 11 日开盘进场，当日收盘为持有第 1 天
    assert "AAA" in held(inp, 39)  # 第 29 天
    assert "AAA" not in held(inp, 40)  # 第 30 天收盘离场


def test_earnings_no_entry_and_exit() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    # 反应日在 5 个交易日内：不进场
    assert held(inp, 10, EventCalendar(earnings={"AAA": (d(14),)})) == {}
    # 第 6 个交易日：可以进场
    assert "AAA" in held(inp, 10, EventCalendar(earnings={"AAA": (d(16),)}))
    # 持有中，反应日为 t+2 → 当日离场；t+3 → 仍持有
    ev = EventCalendar(earnings={"AAA": (d(20),)})
    assert "AAA" in held(inp, 17, ev) and "AAA" not in held(inp, 18, ev)
    # 没有未来记录：正常进场
    assert "AAA" in held(inp, 10, EventCalendar(earnings={"AAA": (d(2),)}))


def test_fomc_no_entry() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    inp.long_sig.loc[IDX[11], "AAA"] = True
    ev = EventCalendar(fomc=(d(11),))
    assert held(inp, 10, ev) == {}  # 次日是 FOMC
    assert "AAA" in held(inp, 11, ev)


def test_fomc_exit_option() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "AAA"] = True
    ev = EventCalendar(fomc=(d(16),))
    assert "AAA" in held(inp, 15, ev)
    assert "AAA" not in held(inp, 15, ev, replace(P, fomc_exit=True))


def test_max_positions_and_ranking() -> None:
    inp = make_inputs()
    for k, s in enumerate(STOCKS):
        inp.long_sig.loc[IDX[10], s] = True
        inp.score.loc[IDX[10], s] = [0.1, 0.6, 0.3, 0.5, 0.5, 0.2][k]
    assert set(held(inp, 10)) == {"BBB", "DDD", "EEE", "CCC"}
    inp.score.loc[IDX[10], "CCC"] = 0.5  # 四只并列 0.5：按代码字母序取 AAA、CCC、DDD
    inp.score.loc[IDX[10], "AAA"] = 0.5
    assert set(held(inp, 10)) == {"BBB", "AAA", "CCC", "DDD"}


def test_qqq_psq_exclusive_and_psq_exit() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], "QQQ"] = True
    inp.short_sig.iloc[12] = True
    assert "QQQ" in held(inp, 12) and "PSQ" not in held(inp, 12)

    inp = make_inputs()
    inp.short_sig.iloc[10] = True
    inp.close.loc[IDX[10] :, "QQQ"] = 98.0  # QQQ 在 5 日线（99）下方
    inp.long_sig.loc[IDX[12], "QQQ"] = True
    assert "PSQ" in held(inp, 12) and "QQQ" not in held(inp, 12)
    inp.close.loc[IDX[14], "QQQ"] = 100.0  # QQQ 收复 5 日线 → PSQ 离场
    assert "PSQ" in held(inp, 13) and "PSQ" not in held(inp, 14)


def test_weight_caps() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], ["AAA", "BBB"]] = True
    inp.vol.loc[IDX[10], "AAA"] = 0.04
    inp.vol.loc[IDX[10], "BBB"] = 0.01
    w = held(inp, 10)
    assert w["AAA"] == pytest.approx(0.25 * 0.025 / 0.04)  # 高波动 < 0.25
    assert w["BBB"] == pytest.approx(0.30)  # 低波动封顶 0.30
    assert sum(w.values()) <= 1


def test_weights_scaled_when_over_one() -> None:
    inp = make_inputs()
    inp.long_sig.loc[IDX[10], ["AAA", "BBB"]] = True
    inp.vol.loc[IDX[10], "AAA"] = 0.01
    inp.vol.loc[IDX[10], "BBB"] = 0.04  # AAA 0.30（封顶），BBB 0.25 × 0.025 / 0.04 = 0.15625
    inp.long_sig.loc[IDX[11], ["CCC", "DDD"]] = True
    inp.vol.loc[IDX[11], ["AAA", "BBB"]] = 0.05
    inp.vol.loc[IDX[11], ["CCC", "DDD"]] = 0.005  # 均值 0.0275 → CCC、DDD 原始权重封顶 0.30
    w = held(inp, 11)  # 0.30 + 0.15625 + 0.60 > 1 → 新仓位按比例缩放到剩余的 0.54375
    assert w["AAA"] == pytest.approx(0.30) and w["BBB"] == pytest.approx(0.15625)
    assert w["CCC"] == pytest.approx(0.54375 / 2) and w["DDD"] == pytest.approx(0.54375 / 2)
    assert sum(w.values()) == pytest.approx(1.0)


# ---- compute_inputs 与端到端 ----


def _bars(prices: np.ndarray, seed: int = 0) -> pd.DataFrame:
    """日内振幅随机（收盘不总在振幅中间），使“收在当日高位”等确认条件可能出现。"""
    rng = np.random.default_rng(seed)
    n = len(prices)
    return normalize_bars(
        pd.DataFrame(
            {
                "open": prices * (1 + rng.normal(0, 0.005, n)),
                "high": prices * (1 + rng.uniform(0, 0.02, n)),
                "low": prices * (1 - rng.uniform(0, 0.02, n)),
                "close": prices,
                "adj_close": prices,
                "volume": np.full(n, 1000, dtype="int64"),
            },
            index=IDX[:n],
        )
    )


def _frames(bars: dict[str, pd.DataFrame], as_of: date) -> dict[str, pd.DataFrame]:
    load = field_loader_from_bars(bars)
    return {f: load(SYMS, f, None, as_of) for f in ("open", "high", "low", "close", "volume")}


def test_missing_symbol_data() -> None:
    n = len(IDX)
    bars = {s: _bars(100 * 1.002 ** np.arange(n)) for s in SYMS if s != "FFF"}
    frames = _frames(bars, DAYS[-1])  # FFF 整列 NaN
    params = replace(P, trend_ma=20, support_ma=5, exit_ma=3, replay_days=60)
    inp = compute_inputs(frames, params)
    w = simulate(inp, params, EventCalendar(), CAL, DAYS[-1])
    assert "FFF" not in w


def test_no_look_ahead() -> None:
    rng = np.random.default_rng(7)
    n = len(IDX)
    bars = {s: _bars(100 * np.cumprod(1 + rng.normal(0.001, 0.02, n))) for s in SYMS}
    params = replace(P, trend_ma=20, support_ma=5, exit_ma=3, replay_days=60)
    as_of = DAYS[100]
    full = simulate(
        compute_inputs(_frames(bars, DAYS[-1]), params), params, EventCalendar(), CAL, as_of
    )
    cut = simulate(
        compute_inputs(_frames(bars, as_of), params), params, EventCalendar(), CAL, as_of
    )
    assert full == cut


def test_end_to_end_target_weights() -> None:
    rng = np.random.default_rng(3)
    n = len(IDX)
    bars = {s: _bars(100 * np.cumprod(1 + rng.normal(0.002, 0.02, n)), k)
            for k, s in enumerate(SYMS)}  # fmt: skip
    strat = SwingPullback()
    params = {"stocks": list(STOCKS), "trend_ma": 20, "support_ma": 5, "exit_ma": 3,
              "replay_days": 60}  # fmt: skip
    load = field_loader_from_bars(bars)
    seen = []
    for as_of in DAYS[60:]:
        ctx = Context(as_of=as_of, loader=lambda s, a, b: pd.DataFrame(), params=params,
                      field_loader=load)  # fmt: skip
        w = strat.run(ctx)
        assert sum(w.values()) <= 1 + 1e-9 and len(w) <= 4
        assert not ({"QQQ", "PSQ"} <= set(w))
        seen.append(w)
    assert any(seen)  # 随机游走数据中至少出现过持仓


def test_registered_and_params() -> None:
    s = get_strategy("swing_pullback")
    assert isinstance(s, SwingPullback) and s.min_weight_diff == 0.10
    assert s.symbols({}) == ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "MU",
                             "QQQ", "PSQ"]  # fmt: skip
    assert s.warmup_days({}) == 120 + 250 + 30
    with pytest.raises(ValueError, match="bogus"):
        SwingParams.from_mapping({"bogus": 1})


def test_inverse_disabled() -> None:
    p = replace(P, use_inverse=False)
    assert "PSQ" not in p.symbols
    inp = make_inputs()
    inp.short_sig.iloc[10] = True
    inp.close.loc[IDX[10] :, "QQQ"] = 98.0  # QQQ 在 5 日线（99）下方，PSQ 不会立即离场
    inp.score.loc[:, "PSQ"] = 0.5
    assert "PSQ" not in held(inp, 12, params=p)
    assert "PSQ" in held(inp, 12)  # 默认仍会买 PSQ


def test_inverse_disabled_without_psq_data() -> None:
    n = len(IDX)
    p = replace(P, trend_ma=20, support_ma=5, exit_ma=3, replay_days=60, use_inverse=False)
    bars = {s: _bars(100 * 1.002 ** np.arange(n)) for s in p.symbols}
    load = field_loader_from_bars(bars)
    frames = {f: load(p.symbols, f, None, DAYS[-1]) for f in FIELDS}
    w = simulate(compute_inputs(frames, p), p, EventCalendar(), CAL, DAYS[-1])
    assert "PSQ" not in w
