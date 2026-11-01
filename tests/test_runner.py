"""回测运行：tiingo 模式只读本地数据、支持候选池策略、记录数据覆盖（#66 Task 3）。"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quant.broker.sim import CostModel
from quant.core.calendar import get_calendar
from quant.core.settings import Settings
from quant.data.backtest_data import backtest_store, earnings_path
from quant.data.events import save_earnings
from quant.data.providers.base import DataProvider
from quant.data.universe import constituents_path
from quant.engine import runner
from quant.engine.runner import BacktestDataError, BacktestSpec, run_spec
from quant.strategies import get_strategy

CAL = get_calendar()
END = date(2025, 6, 30)
COSTS = CostModel(Decimal("0.005"), Decimal("1"), Decimal("0.0005"))
COMBO_PARAMS: dict[str, Any] = {
    "swing": {"stocks": ["S1"], "trend_ma": 20, "support_ma": 5, "exit_ma": 3, "replay_days": 60},
    "momentum": {"top_n": 2},
}


def bars(growth: float, end: date = END) -> pd.DataFrame:
    days = pd.DatetimeIndex(CAL.trading_days(date(2013, 1, 2), end), name="date")
    t = np.arange(len(days))
    close = 100 * (1 + growth) ** t * (1 + 0.01 * np.sin(t / 5))
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
         "adj_close": close, "volume": np.full(len(days), 2_000_000, dtype="int64")},
        index=days,
    )  # fmt: skip


def settings(tmp_path: Path) -> Settings:
    return Settings(broker="sim", TRADING_MODE="sim", data_dir=tmp_path,
                    data={"backtest_source": "tiingo"})  # fmt: skip


class Exploding(DataProvider):
    source = "boom"

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise AssertionError(f"tiingo 模式不应联网：{symbol}")


def write_fixed(tmp_path: Path, symbols: list[str]) -> None:
    store = backtest_store(tmp_path)
    for i, s in enumerate(symbols):
        store.write_bars(s, bars(0.0003 + i * 0.0001), "tiingo")


def test_tiingo_mode_does_not_fetch(tmp_path: Path) -> None:
    syms = sorted({*get_strategy("etf_momentum").symbols({}), "SPY"})
    write_fixed(tmp_path, syms)
    spec = BacktestSpec("etf_momentum", date(2025, 1, 2), END, COSTS)
    res = run_spec(spec, settings(tmp_path), provider=Exploding())
    assert len(res.equity) > 100
    assert res.data_info == {"source": "tiingo", "data_end": END.isoformat()}


def test_missing_fixed_symbols_fails_with_list(tmp_path: Path) -> None:
    write_fixed(tmp_path, ["SPY"])
    spec = BacktestSpec("etf_momentum", date(2025, 1, 2), END, COSTS)
    with pytest.raises(BacktestDataError, match="回测数据尚未补齐") as e:
        run_spec(spec, settings(tmp_path))
    assert "QQQ" in str(e.value) and "SPY" not in str(e.value).split("缺")[1]


def test_end_after_local_data_fails(tmp_path: Path) -> None:
    syms = sorted({*get_strategy("etf_momentum").symbols({}), "SPY"})
    write_fixed(tmp_path, syms)
    spec = BacktestSpec("etf_momentum", date(2025, 1, 2), date(2025, 9, 30), COSTS)
    with pytest.raises(BacktestDataError, match="回测数据尚未补齐"):
        run_spec(spec, settings(tmp_path))


def _combo_setup(tmp_path: Path) -> None:
    write_fixed(tmp_path, ["QQQ", "S1", "SPY"])
    store = backtest_store(tmp_path)
    for s, g in (("A", 0.0012), ("B", 0.0009), ("C", 0.0006)):  # D 没有数据
        store.write_bars(s, bars(g), "tiingo")
    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('date,tickers\n2013-01-02,"A,B,C,D"\n', encoding="utf-8")


def test_universe_strategy_runs_with_partial_coverage(tmp_path: Path) -> None:
    _combo_setup(tmp_path)
    spec = BacktestSpec("combo", date(2025, 1, 2), END, COSTS, params=COMBO_PARAMS)
    res = run_spec(spec, settings(tmp_path), provider=Exploding())
    info = res.data_info
    assert info["source"] == "tiingo" and info["coverage"] == pytest.approx(0.75)
    assert info["missing_by_year"] == {"2025": pytest.approx(0.25)}
    assert info["earnings"] == "none"
    assert any(w for w in res.signals.values())


def test_events_loaded_for_universe_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _combo_setup(tmp_path)
    save_earnings(earnings_path(tmp_path), {"A": [date(2025, 4, 30)]})
    seen: dict[str, Any] = {}
    real = runner.Backtester  # type: ignore[attr-defined]

    def spy(*args: Any, **kw: Any) -> Any:
        seen.update(kw)
        return real(*args, **kw)

    monkeypatch.setattr(runner, "Backtester", spy)
    spec = BacktestSpec("combo", date(2025, 1, 2), END, COSTS, params=COMBO_PARAMS)
    res = run_spec(spec, settings(tmp_path))
    assert seen["events"].next_earnings("A", date(2025, 4, 1)) == date(2025, 4, 30)
    assert seen["universe"] is not None
    assert res.data_info["earnings"] == "partial"  # 文件存在但本月同步未完成


def test_universe_without_constituents_fails(tmp_path: Path) -> None:
    write_fixed(tmp_path, ["QQQ", "S1", "SPY"])
    spec = BacktestSpec("combo", date(2025, 1, 2), END, COSTS, params=COMBO_PARAMS)
    with pytest.raises(BacktestDataError, match="成分股"):
        run_spec(spec, settings(tmp_path))


# ---- 最终审查修复（#66）----


def test_start_before_local_data_fails(tmp_path: Path) -> None:
    syms = sorted({*get_strategy("etf_momentum").symbols({}), "SPY"})
    write_fixed(tmp_path, syms)
    spec = BacktestSpec("etf_momentum", date(2010, 1, 4), END, COSTS)
    with pytest.raises(BacktestDataError, match="开始日期过早"):
        run_spec(spec, settings(tmp_path))


def test_earnings_partial_until_sync_complete(tmp_path: Path) -> None:
    from quant.data.backtest_data import BackfillState, state_path

    _combo_setup(tmp_path)
    save_earnings(earnings_path(tmp_path), {"A": [date(2025, 4, 30)]})
    spec = BacktestSpec("combo", date(2025, 1, 2), END, COSTS, params=COMBO_PARAMS)
    assert run_spec(spec, settings(tmp_path)).data_info["earnings"] == "partial"
    st = BackfillState.load(state_path(tmp_path))
    st.earnings_month = "2026-11"
    st.save()
    assert run_spec(spec, settings(tmp_path)).data_info["earnings"] == "complete"


def test_coverage_uses_snapshot_periods(tmp_path: Path) -> None:
    """D 的数据停在 2024 年（预热期内）：2025 年的回测区间里它仍算缺失。"""
    _combo_setup(tmp_path)
    backtest_store(tmp_path).write_bars("D", bars(0.0003, end=date(2024, 6, 28)), "tiingo")
    spec = BacktestSpec("combo", date(2025, 1, 2), END, COSTS, params=COMBO_PARAMS)
    assert run_spec(spec, settings(tmp_path)).data_info["coverage"] == pytest.approx(0.75)


def test_stale_holdings_reported(tmp_path: Path) -> None:
    """持仓标的数据在区间结束前中断（退市）：记入 metrics.data.stale_holdings。"""
    _combo_setup(tmp_path)
    store = backtest_store(tmp_path)
    store.write_bars("A", bars(0.0012, end=date(2025, 5, 30)), "tiingo")  # A 涨得最快，必被持有
    import shutil

    shutil.rmtree(store.root / "bars" / "daily" / "symbol=A")
    store.write_bars("A", bars(0.0012, end=date(2025, 5, 30)), "tiingo")
    spec = BacktestSpec("combo", date(2025, 1, 2), END, COSTS, params=COMBO_PARAMS)
    info = run_spec(spec, settings(tmp_path)).data_info
    assert info["stale_holdings"] == {"A": "2025-05-30"}
