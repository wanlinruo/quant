"""命令行回测端到端测试：假数据源 + 临时数据目录，不访问网络、不写数据库。"""

import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.cli import build_parser, main, run_backtest
from quant.core.calendar import get_calendar
from quant.core.settings import Settings
from quant.data.pipeline import DataValidationError
from quant.data.providers.base import DataProvider, normalize_bars
from quant.data.store import ParquetStore

SYMS = {"SPY": 0.0004, "QQQ": 0.0006, "TLT": 0.0001, "GLD": 0.0002, "SHY": 0.00008}


class FakeProvider(DataProvider):
    source = "fake"

    def __init__(self, broken: str | None = None) -> None:
        self.calls: list[str] = []
        self.broken = broken

    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append(symbol)
        cal = get_calendar()
        days = pd.DatetimeIndex(cal.trading_days(start, end))
        rng = np.random.default_rng(sum(map(ord, symbol)))
        adj = 100 * np.cumprod(1 + rng.normal(SYMS[symbol], 0.01, len(days)))
        df = pd.DataFrame(
            {"open": adj, "high": adj * 1.01, "low": adj * 0.99, "close": adj, "adj_close": adj,
             "volume": np.full(len(days), 1000, dtype="int64")},
            index=days,
        )  # fmt: skip
        if symbol == self.broken:
            df = df.drop(df.index[10])  # 缺一个交易日
        return normalize_bars(df)


def settings(tmp_path: Path) -> Settings:
    return Settings(broker="sim", data_dir=tmp_path, TRADING_MODE="sim")


ARGS = ["backtest", "--start", "2024-01-02", "--end", "2025-12-31",
        "--commission-per-share", "0.005", "--min-commission", "1", "--no-db"]  # fmt: skip


def test_end_to_end_backtest(tmp_path: Path) -> None:
    provider, out = FakeProvider(), io.StringIO()
    args = build_parser().parse_args([*ARGS, "--param", "lookback=60"])
    result = run_backtest(args, settings(tmp_path), provider, out)
    text = out.getvalue()
    assert "年化收益" in text and "最大回撤" in text and "基准 SPY" in text
    assert "未写入数据库" in text
    assert result.metrics.trades > 0
    assert sorted(provider.calls) == sorted(SYMS)
    assert ParquetStore(tmp_path).symbols() == sorted(SYMS)

    # 第二次运行直接使用本地 Parquet，不再拉取
    provider2 = FakeProvider()
    run_backtest(args, settings(tmp_path), provider2, io.StringIO())
    assert provider2.calls == []


def test_validation_failure_stops(tmp_path: Path) -> None:
    args = build_parser().parse_args(ARGS)
    with pytest.raises(DataValidationError, match="missing_day"):
        run_backtest(args, settings(tmp_path), FakeProvider(broken="TLT"), io.StringIO())


def test_commission_required(tmp_path: Path) -> None:
    args = build_parser().parse_args(["backtest", "--start", "2024-01-02", "--end", "2025-12-31"])
    with pytest.raises(ValueError, match="#5"):
        run_backtest(args, settings(tmp_path), FakeProvider(), io.StringIO())


def test_param_parsing() -> None:
    args = build_parser().parse_args(
        [*ARGS, "--param", "lookback=60", "--param", "universe=SPY,TLT", "--cash", "5000"]
    )
    assert dict(args.param) == {"lookback": 60, "universe": ["SPY", "TLT"]}
    assert args.cash == Decimal("5000")
    with pytest.raises(SystemExit):
        build_parser().parse_args([*ARGS, "--param", "novalue"])


def test_main_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRADING_MODE", "bogus")
    assert main(ARGS) == 1
    assert "TRADING_MODE" in capsys.readouterr().err


# ---- quant preview（#47 第二份计划任务 7） ----


def test_preview_combo_without_db(tmp_path: Path) -> None:
    from quant.cli import preview
    from quant.data.universe import constituents_path
    from quant.jobs.daily import JobContext
    from test_jobs import COMBO_PARAMS, DAY, UNIVERSE_CSV, TigerLike, UniverseFake

    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(UNIVERSE_CSV, encoding="utf-8")
    cfg = Settings(broker="tiger", data_dir=tmp_path, data={"primary": "tiger"},
                   risk={"symbol_whitelist": ["QQQ"], "max_order_usd": "150000"})  # fmt: skip
    args = build_parser().parse_args(
        ["preview", "--strategy", "combo", "--date", DAY.isoformat(), "--sync",
         "--universe", "sina", "--param", f"swing={json.dumps(COMBO_PARAMS['swing'])}"]
    )  # fmt: skip
    made: list[JobContext] = []

    def factory(s: Settings, notifier: object) -> JobContext:
        assert s.strategy.name == "combo" and s.data.universe == "sina"
        ctx = JobContext(s, None, notifier, ParquetStore(tmp_path), TigerLike(),  # type: ignore[arg-type]
                         universe_provider=UniverseFake())  # fmt: skip
        ctx.sleep = lambda x: None
        made.append(ctx)
        return ctx

    out = io.StringIO()
    assert preview(args, cfg, out, ctx_factory=factory) == 0
    text = out.getvalue()
    assert "覆盖率" in text and "目标仓位" in text and "AAA" in text
    assert "择时系数" in text and "动量排名" in text
    assert made[0].engine is None  # 不连接数据库：不写 signals / job_runs


def test_preview_shows_strategy_own_ranking_and_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 预览直接用策略自己的排序与择时计算，不另算一遍（数据起点不一致会导致结果不同，#56）
    from quant.strategies.combo import Combo
    from quant.strategies.momentum_select import MomentumSelect

    monkeypatch.setattr(MomentumSelect, "ranking", lambda self, ctx, me, p: ["ZZZ", "YYY"])
    monkeypatch.setattr(Combo, "timing_factor", lambda self, ctx, p: 0.5)
    out = _run_preview(tmp_path)
    assert "前 10：ZZZ, YYY" in out and "择时系数：0.5" in out


def _run_preview(tmp_path: Path) -> str:
    from quant.cli import preview
    from quant.data.universe import constituents_path
    from quant.jobs.daily import JobContext
    from test_jobs import COMBO_PARAMS, DAY, UNIVERSE_CSV, TigerLike, UniverseFake

    path = constituents_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNIVERSE_CSV, encoding="utf-8")
    cfg = Settings(broker="tiger", data_dir=tmp_path, data={"primary": "tiger"},
                   risk={"symbol_whitelist": ["QQQ"], "max_order_usd": "150000"})  # fmt: skip
    args = build_parser().parse_args(
        ["preview", "--strategy", "combo", "--date", DAY.isoformat(), "--sync",
         "--universe", "sina", "--param", f"swing={json.dumps(COMBO_PARAMS['swing'])}"]
    )  # fmt: skip

    def factory(s: Settings, notifier: object) -> JobContext:
        ctx = JobContext(s, None, notifier, ParquetStore(tmp_path), TigerLike(),  # type: ignore[arg-type]
                         universe_provider=UniverseFake())  # fmt: skip
        ctx.sleep = lambda x: None
        return ctx

    out = io.StringIO()
    assert preview(args, cfg, out, ctx_factory=factory) == 0
    return out.getvalue()


@pytest.mark.parametrize(
    "error",
    [RuntimeError("策略 combo 需要候选池数据"), OSError("network down")],
)
def test_preview_errors_are_short(
    error: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 预览是人工排查用的命令：出错时给一行中文错误，不打印堆栈（#56）
    import quant.cli
    from quant.data.providers.base import DataUnavailableError

    for e in (error, DataUnavailableError("新浪拉取 AAPL 失败")):

        def boom(*a: object, _e: Exception = e, **k: object) -> int:
            raise _e

        monkeypatch.setattr(quant.cli, "preview", boom)
        monkeypatch.setenv("TRADING_MODE", "sim")
        assert main(["preview", "--strategy", "combo"]) == 1
        err = capsys.readouterr().err
        assert err.startswith("错误：") and str(e) in err and "Traceback" not in err
