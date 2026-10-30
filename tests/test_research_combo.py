"""组合策略研究脚本的纯函数（#47 Task 7）。"""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from quant.core.calendar import TradingCalendar
from quant.data.universe import parse_sp500_history
from quant.strategies.combo import ComboParams

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research" / "combo.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("combo_research", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["combo_research"] = module
    spec.loader.exec_module(module)
    return module


R = _load()


def test_configs() -> None:
    assert len(R.CONFIGS) == 10
    assert R.CONFIGS["组合（持有过财报）"] == {}
    assert R.CONFIGS["组合（财报前离场）"] == {"momentum": {"earnings_exit": True}}
    assert len(R.NEIGHBORS) == 6
    for cfg in R.CONFIGS.values():
        ComboParams.from_mapping(cfg)  # 全部参数合法


def test_missing_ratio_by_year() -> None:
    u = parse_sp500_history('date,tickers\n2015-01-02,"A,B,C,D"\n2016-01-04,"A,B"\n')
    r = R.missing_ratio_by_year(u, {"A", "B"}, date(2015, 1, 2), date(2016, 12, 30))
    assert r[2015] == pytest.approx(0.5)
    assert r[2016] == pytest.approx(0.0)


def test_split_metrics_continuous() -> None:
    days = TradingCalendar().trading_days(date(2022, 12, 1), date(2023, 1, 31))
    eq = pd.Series(100 * 1.001 ** np.arange(len(days)), index=pd.DatetimeIndex(days))
    full, ins, oos = R.split_metrics(eq, date(2023, 1, 3))
    prev = eq[eq.index < pd.Timestamp("2023-01-03")]
    assert ins.end == "2022-12-30"
    assert oos.start == "2022-12-30"  # 从前一交易日收盘起算，不丢失 1-3 当天的收益
    assert oos.total_return == pytest.approx(eq.iloc[-1] / prev.iloc[-1] - 1)
    assert full.total_return == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1)
