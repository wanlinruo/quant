"""combo 组合策略（#47 Task 5）：动量仓 + V2 仓 + 波动率择时。合成数据，不访问网络。"""

import json
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.broker.sim import CostModel
from quant.engine.backtester import Backtester, field_loader_from_bars
from quant.strategies import get_strategy
from quant.strategies.base import Context
from quant.strategies.combo import Combo, ComboParams, merge_weights, vol_factor
from quant.strategies.momentum_select import MomentumParams, MomentumSelect
from quant.strategies.swing_pullback import SwingParams
from quant.strategies.universe import Constituents
from test_momentum_select import CAL, IDX, N, _bars, ctx, make_close, make_volume


def test_vol_factor() -> None:
    calm = pd.Series(100 * np.cumprod(np.full(30, 1.001)))
    wild = pd.Series(100 * np.cumprod(np.tile([1.03, 0.97], 15)))  # 年化约 48%
    assert vol_factor(calm, 20, 0.30, 0.5) == 1.0
    assert vol_factor(wild, 20, 0.30, 0.5) == 0.5
    assert vol_factor(wild.iloc[:10], 20, 0.30, 0.5) == 1.0  # 数据不足
    assert vol_factor(pd.Series([np.nan] * 30), 20, 0.30, 0.5) == 1.0


def test_merge_weights_adds_overlap() -> None:
    out = merge_weights({"MU": 0.25, "WDC": 0.25}, {"MU": 0.25, "AAPL": 0.25}, 0.4, 0.6, 1.0)
    assert out == pytest.approx({"MU": 0.25, "WDC": 0.10, "AAPL": 0.15})
    half = merge_weights({"WDC": 0.25}, {"AAPL": 0.25}, 0.4, 0.6, 0.5)
    assert half == pytest.approx({"WDC": 0.05, "AAPL": 0.075})


def test_params_validation() -> None:
    with pytest.raises(ValueError):
        ComboParams.from_mapping({"momentum_weight": 0.5, "swing_weight": 0.6})
    with pytest.raises(ValueError, match="未知参数"):
        ComboParams.from_mapping({"mw": 0.5})


def test_swing_defaults_are_v2() -> None:
    p = ComboParams()
    assert SwingParams.from_mapping(p.swing_params()).slope_days == 10
    assert not SwingParams.from_mapping(p.swing_params()).use_inverse
    over = ComboParams.from_mapping({"swing": {"exit_ma": 5}}).swing_params()
    assert over["exit_ma"] == 5 and over["slope_days"] == 10  # 只覆盖同名键
    syms = Combo().symbols({})
    assert "PSQ" not in syms and "QQQ" in syms and "NVDA" in syms


def test_momentum_only_matches_momentum_select() -> None:
    combo, mom = Combo(CAL), MomentumSelect(CAL)
    params = {"momentum_weight": 0.4, "swing_weight": 0.0, "vol_scale": 1.0}
    for d in (date(2024, 5, 31), date(2024, 6, 10), date(2024, 6, 28)):
        want = {s: 0.4 * w for s, w in mom.run(ctx(d, {})).items()}
        assert combo.run(ctx(d, params)) == pytest.approx(want)


def test_combo_end_to_end() -> None:
    close = make_close()
    t = np.arange(N)
    close["S1"] = 100 * (1 + 0.002 * np.sin(t / 5)) * 1.001**t  # 上升中有回踩
    close["S2"] = 80 * (1 + 0.003 * np.sin(t / 7)) * 1.0008**t
    close["QQQ"] = 300 * 1.0005**t
    vol = make_volume().reindex(columns=close.columns).fillna(1e6)
    bars = _bars(close, vol)
    params = {
        "swing": {"stocks": ["S1", "S2"], "trend_ma": 20, "support_ma": 5, "exit_ma": 3,
                  "replay_days": 60},
    }  # fmt: skip
    bt = Backtester(
        Combo(CAL),
        bars,
        CAL,
        CostModel(Decimal("0.005"), Decimal("1"), Decimal("0.0005")),
        params=params,
        benchmark=None,
        universe=Constituents({date(2023, 1, 3): frozenset({"A", "B", "C"})}),
    )
    res = bt.run(IDX[-60].date(), IDX[-1].date())
    assert res.signals
    assert all(sum(w.values()) <= 1 + 1e-9 for w in res.signals.values())
    # 动量仓每只 0.4 × 0.25 = 0.10
    assert any(w.get("A") == pytest.approx(0.10) for w in res.signals.values())


def test_registered() -> None:
    s = get_strategy("combo")
    assert s.min_weight_diff == 0.04
    assert s.warmup_days({}) >= 252


def _full_ctx(d: date) -> tuple[Context, dict[str, object]]:
    """动量仓 + V2 仓 + 择时都参与的 Context（合成数据）。"""
    close = make_close()
    t = np.arange(N)
    close["S1"] = 100 * (1 + 0.002 * np.sin(t / 5)) * 1.001**t
    close["S2"] = 80 * (1 + 0.003 * np.sin(t / 7)) * 1.0008**t
    close["QQQ"] = 300 * (1 + 0.08 * np.sin(t / 2)) * 1.0005**t  # 波动大，触发择时
    vol = make_volume().reindex(columns=close.columns).fillna(1e6)
    params: dict[str, object] = {
        "swing": {"stocks": ["S1", "S2"], "trend_ma": 20, "support_ma": 5, "exit_ma": 3,
                  "replay_days": 60},
    }  # fmt: skip
    c = Context(
        as_of=d,
        loader=lambda s, a, b: pd.DataFrame(),
        params=params,
        field_loader=field_loader_from_bars(_bars(close, vol)),
        universe=Constituents({date(2023, 1, 3): frozenset({"A", "B", "C", "E", "F"})}),
    )
    return c, params


@pytest.mark.parametrize("d", [date(2024, 5, 31), date(2024, 6, 10), date(2024, 6, 28)])
def test_breakdown_matches_target_weights(d: date) -> None:
    c, _ = _full_ctx(d)
    b = Combo(CAL).breakdown(c)
    assert Combo(CAL).target_weights(c) == b.weights
    assert merge_weights(b.momentum, b.swing, 0.4, 0.6, b.factor) == b.weights
    assert b.ranking[: len(b.momentum)] == sorted(b.momentum, key=b.ranking.index)
    assert b.universe_size == 5


def test_run_explained_detail_shape() -> None:
    c, _ = _full_ctx(date(2024, 6, 10))
    weights, detail = Combo(CAL).run_explained(c)
    assert weights == Combo(CAL).run(c)
    assert detail is not None
    assert set(detail) == {
        "sleeves", "sleeve_weights", "timing_factor", "vol_symbol", "vol_20d", "vol_threshold",
        "momentum_rank", "momentum_rank_asof", "universe_size", "rebalance_day", "top_n",
    }  # fmt: skip
    assert set(detail["sleeves"]) == {"momentum", "swing"}
    assert detail["sleeve_weights"] == {"momentum": 0.4, "swing": 0.6}
    assert len(detail["momentum_rank"]) <= 50
    assert detail["rebalance_day"] == "2024-05-31"
    assert detail["vol_symbol"] == "QQQ" and detail["vol_threshold"] == 0.30
    assert isinstance(detail["vol_20d"], float)
    json.dumps(detail)  # 能写入 JSONB


def test_run_explained_default_is_none() -> None:
    weights, detail = MomentumSelect(CAL).run_explained(ctx(date(2024, 6, 10), {}))
    assert detail is None and weights == MomentumSelect(CAL).run(ctx(date(2024, 6, 10), {}))


@pytest.mark.parametrize(
    ("amp", "digest"),
    [
        (0.0, "0a628adf9ee47115f883fd5980976d06ac729907960216fc948356d733511aad"),
        (0.08, "7f799b6498e41cb825964387a927ee365e0f38f7aeb1732f368b46d0f5f259f2"),
    ],
)
def test_backtest_signals_unchanged(amp: float, digest: str) -> None:
    """重构前记录的回测信号摘要：引入 breakdown 后目标权重逐位不变（#65 计划约束）。"""
    import hashlib

    close = make_close()
    t = np.arange(N)
    close["S1"] = 100 * (1 + 0.002 * np.sin(t / 5)) * 1.001**t
    close["S2"] = 80 * (1 + 0.003 * np.sin(t / 7)) * 1.0008**t
    close["QQQ"] = 300 * (1 + amp * np.sin(t / 2)) * 1.0005**t
    vol = make_volume().reindex(columns=close.columns).fillna(1e6)
    params = {
        "swing": {"stocks": ["S1", "S2"], "trend_ma": 20, "support_ma": 5, "exit_ma": 3,
                  "replay_days": 60},
    }  # fmt: skip
    bt = Backtester(
        Combo(CAL), _bars(close, vol), CAL,
        CostModel(Decimal("0.005"), Decimal("1"), Decimal("0.0005")),
        params=params, benchmark=None,
        universe=Constituents({date(2023, 1, 3): frozenset({"A", "B", "C"})}),
    )  # fmt: skip
    res = bt.run(IDX[-60].date(), IDX[-1].date())
    blob = json.dumps({str(k): v for k, v in sorted(res.signals.items())}, sort_keys=True)
    assert hashlib.sha256(blob.encode()).hexdigest() == digest


def test_run_explained_ranks_as_of_signal_day() -> None:
    """信号日当天的动量排名单独保存（调仓日排名只反映选股时，整月不变）。"""
    c, _ = _full_ctx(date(2024, 6, 10))
    combo = Combo(CAL)
    _, detail = combo.run_explained(c)
    assert detail is not None
    p = ComboParams.from_mapping(c.params)
    want = combo.momentum.ranking(
        c, date(2024, 6, 10), MomentumParams.from_mapping(p.momentum_params())
    )
    assert detail["momentum_rank_asof"] == want[:50]
