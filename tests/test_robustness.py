"""稳健性检验的子任务计划（#68）。"""

from datetime import date

import pandas as pd
import pytest

from quant.engine.robustness import (
    Variant,
    grid_axes,
    plan,
    scale_costs,
    skipped_neighbor_labels,
    walk_forward,
)

S, E = date(2016, 1, 4), date(2026, 10, 30)


def by_kind(vs: list[Variant], kind: str) -> list[Variant]:
    return [v for v in vs if v.kind == kind]


def test_combo_grid_neighbors_match_research_definition() -> None:
    vs = plan("combo", {"momentum_weight": 0.4, "swing_weight": 0.6}, S, E)
    grid = by_kind(vs, "grid")
    assert len(grid) == 8  # 3×3 去掉中心（父回测本身）
    assert all((v.x, v.y) != (1, 1) for v in grid)
    cell = next(v for v in grid if (v.x, v.y) == (0, 2))
    assert cell.params["momentum_weight"] == pytest.approx(0.3)
    assert cell.params["swing_weight"] == pytest.approx(0.7)
    assert cell.params["vol_threshold"] == pytest.approx(0.35)
    nb = sorted(v.label for v in vs if v.neighbor)
    assert len(nb) == 6
    tops = [v.params["momentum"]["top_n"] for v in by_kind(vs, "extra")]
    assert sorted(tops) == [3, 5]
    assert all(v.start == S for v in grid)


def test_extra_keeps_other_nested_params() -> None:
    vs = plan("combo", {"momentum": {"top_n": 6, "lookback": 100}}, S, E)
    tops = sorted(v.params["momentum"]["top_n"] for v in by_kind(vs, "extra"))
    assert tops == [5, 7]
    assert all(v.params["momentum"]["lookback"] == 100 for v in by_kind(vs, "extra"))


def test_out_of_range_cells_skipped() -> None:
    vs = plan("etf_momentum", {"universe": ["SPY", "QQQ"], "top_k": 1}, S, E)
    ys = {v.y for v in by_kind(vs, "grid")}
    assert 0 not in ys  # top_k 0 无效
    assert all(1 <= v.params["top_k"] <= 2 for v in by_kind(vs, "grid"))
    assert skipped_neighbor_labels("etf_momentum", {"universe": ["SPY", "QQQ"], "top_k": 1}) == [
        "动量回看天数 126 · 持有只数 0"
    ]
    assert skipped_neighbor_labels("combo", {"momentum_weight": 0.4}) == []


def test_costs_and_starts() -> None:
    vs = plan("swing_pullback", {}, S, E)
    assert sorted(v.cost_mult or 0 for v in by_kind(vs, "cost")) == [0, 2, 3]
    assert [v.start for v in by_kind(vs, "start")] == [
        date(2016, 4, 4),
        date(2016, 7, 4),
        date(2016, 10, 4),
        date(2017, 1, 4),
    ]
    short = plan("swing_pullback", {}, date(2024, 1, 2), date(2026, 3, 1))
    assert by_kind(short, "start") == []  # 剩余不足 2 年


def test_unknown_strategy_has_no_plan() -> None:
    with pytest.raises(ValueError):
        plan("nope", {}, S, E)


def test_scale_costs() -> None:
    raw = {
        "commission_per_share": "0.0049",
        "min_commission": "0.99",
        "slippage": "0.0005",
        "max_fee_pct": "0.01",
    }
    out = scale_costs(raw, 2)
    assert out == {
        "commission_per_share": "0.0098",
        "min_commission": "1.98",
        "slippage": "0.0010",
        "max_fee_pct": "0.01",
    }
    assert scale_costs(raw, 0)["slippage"] == "0.0000"


def _eq(
    daily: dict[int, float], start: str = "2016-01-04", end: str = "2020-12-31"
) -> "pd.Series[float]":
    idx = pd.bdate_range(start, end)
    r = pd.Series([daily.get(d.year, 0.0) for d in idx], index=idx)
    return (1 + r).cumprod() * 100


def test_walk_forward_picks_best_training_cell_each_year() -> None:
    # a 前三年好、之后差；b 相反；中心 c 一直平稳
    a = _eq({2016: 0.001, 2017: 0.001, 2018: 0.001, 2019: -0.001, 2020: -0.001})
    b = _eq({2016: -0.0005, 2017: -0.0005, 2018: 0.0, 2019: 0.002, 2020: 0.002})
    c = _eq({y: 0.0003 for y in range(2016, 2021)})
    wf = walk_forward({"a": a, "b": b, "c": c}, "c", train_years=3)
    assert wf is not None
    assert [s.year for s in wf.segments] == [2019, 2020]
    assert wf.segments[0].chosen == "a"  # 2016–2018 训练期 a 最好
    assert wf.segments[0].test_return < 0 < wf.segments[0].base_return
    assert wf.segments[1].chosen in ("b", "c")
    assert wf.oos.index[0].year == 2019 and wf.base.index[0].year == 2019


def test_walk_forward_needs_enough_history() -> None:
    s = _eq({}, end="2018-06-30")
    assert walk_forward({"x": s, "c": s}, "c", train_years=3) is None


def test_grid_axes_labels() -> None:
    (xl, xs), (yl, ys) = grid_axes("combo", {"momentum_weight": 0.4})
    assert xl == "动量仓比例" and xs == ["30%", "40%", "50%"]
    assert yl == "择时波动阈值" and ys == ["25%", "30%", "35%"]
