"""研究脚本的参数网格与选择逻辑（swing_pullback Task 7），不跑真实回测。"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research" / "swing_pullback.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("swing_research", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["swing_research"] = module
    spec.loader.exec_module(module)
    return module


R = _load()


def test_grid_size() -> None:
    assert len(R.GRID) == 36
    assert all(set(g) == {"trend_ma", "support_ma", "confirm", "exit_ma"} for g in R.GRID)
    assert len({tuple(sorted(g.items())) for g in R.GRID}) == 36


def test_neighbors() -> None:
    best = {"trend_ma": 120, "support_ma": 20, "confirm": "close_strength", "exit_ma": 5}
    nb = R.neighbors(best)
    assert len(nb) == 6
    assert all(sum(n[k] != best[k] for k in best) == 1 for n in nb)


def test_select_best_tiebreak() -> None:
    results = pd.DataFrame(
        [
            {"trend_ma": 120, "support_ma": 20, "confirm": "rsi2", "exit_ma": 5,
             "is_sharpe": 1.0, "is_max_drawdown": -0.30},
            {"trend_ma": 50, "support_ma": 20, "confirm": "rsi2", "exit_ma": 5,
             "is_sharpe": 1.0, "is_max_drawdown": -0.20},
            {"trend_ma": 50, "support_ma": 30, "confirm": "rsi2", "exit_ma": 5,
             "is_sharpe": 0.9, "is_max_drawdown": -0.10},
        ]
    )  # fmt: skip
    assert R.select_best(results) == {"trend_ma": 50, "support_ma": 20, "confirm": "rsi2",
                                      "exit_ma": 5}  # fmt: skip
