import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pandas as pd

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase0" / "data_check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("data_check", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["data_check"] = module
    spec.loader.exec_module(module)
    return module


dc = _load()

D = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]


def _s(values: list[float], dates: list[date] = D) -> pd.Series:
    return pd.Series(values, index=dates)


def test_identical_series_pass() -> None:
    c = dc.compare_closes("X", _s([100, 101, 102, 103, 104]), _s([100, 101, 102, 103, 104]))
    assert c.n_common == 5
    assert c.coverage == 1.0
    assert c.max_diff == 0.0
    assert c.failures() == []


def test_missing_dates_reported_and_coverage_fails() -> None:
    tiger = _s([100, 101, 102], D[:3])
    c = dc.compare_closes("X", tiger, _s([100, 101, 102, 103, 104]))
    assert c.only_yf == D[3:]
    assert c.only_tiger == []
    assert c.coverage == 0.6
    assert any("覆盖率" in f for f in c.failures())


def test_large_price_difference_fails() -> None:
    c = dc.compare_closes("X", _s([100, 101, 102, 103, 110]), _s([100, 101, 102, 103, 104]))
    assert c.max_diff_date == D[4]
    assert c.n_over_01pct == 1
    assert any("最大误差" in f for f in c.failures())


def test_nan_values_are_ignored() -> None:
    c = dc.compare_closes(
        "X", _s([100, float("nan"), 102, 103, 104]), _s([100, 101, 102, 103, 104])
    )
    assert c.n_tiger == 4
    assert c.only_yf == [D[1]]
