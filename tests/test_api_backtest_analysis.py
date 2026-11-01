"""回测分析接口（#67 A 批 Task 4）：测试库 + 本地样本行情。"""

import json
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api_fixtures import needs_db
from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.data.store import ParquetStore
from quant.db.backtests import complete_run, create_run
from quant.engine.ledger import Ledger

pytestmark = needs_db
IDX = pd.bdate_range("2016-01-04", periods=2700)


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


def prices(seed: int, drift: float) -> pd.Series:
    r = np.random.default_rng(seed).normal(drift, 0.011, len(IDX))
    r[0] = 0
    return pd.Series(100 * np.cumprod(1 + r), index=IDX)


def write_bench(data_dir: Path, symbols: tuple[str, ...] = ("QQQ", "SPY")) -> None:
    store = ParquetStore(data_dir)
    for i, s in enumerate(symbols):
        px = prices(10 + i, 0.0004)
        df = pd.DataFrame({"open": px, "high": px, "low": px, "close": px * 0.98, "adj_close": px,
                           "volume": np.full(len(px), 1000, dtype="int64")},
                          index=pd.DatetimeIndex(px.index, name="date"))  # fmt: skip
        store.write_bars(s, df, "test")


def sample_ledger() -> "Ledger":
    from quant.engine.ledger import Ledger, TradeRow

    d = [x.date() for x in IDX]
    trades = [
        TradeRow(
            1, "AAA", "momentum", d[10], 10.0, 100, d[40], 12.0, 0.2, 200.0, "动量排名跌出前 10"
        ),
        TradeRow(2, "BBB", "swing", d[20], 50.0, 10, d[25], 45.0, -0.1, -50.0, "跌破支撑线"),
        TradeRow(3, "AAA", "swing", d[30], 11.0, 10, d[33], 11.5, 0.045, 5.0, "跌破支撑线"),
        TradeRow(4, "CCC", "momentum", d[2600], 20.0, 5, None, None, 0.05, 5.0, None),
    ]
    positions = [(x, "AAA", "momentum", 0.3) for x in d[10:40]]
    positions += [(x, "BBB", "swing", 0.1) for x in d[20:25]]
    daily: list[tuple[date, float, float | None, dict[str, float]]] = [
        (x, 0.6, 0.5 if 100 <= i < 120 else 1.0, {"momentum": 1.0, "swing": 0.5})
        for i, x in enumerate(d)
    ]
    return Ledger(trades, positions, daily)


@pytest.fixture
def setup(
    db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[TestClient, dict[str, int]]]:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    write_bench(tmp_path)
    eng = create_engine(db_url)
    eq = prices(1, 0.0006) * 1000
    dd = eq / eq.cummax() - 1
    with Session(eng) as s, s.begin():
        from quant.core.settings import CostSettings

        costs = {k: str(v) for k, v in CostSettings().model_dump().items() if v is not None}
        done = create_run(s, "etf_momentum", {"lookback": 99, "top_k": 2, "_cash": "100000",
                                              "universe": ["SPY", "QQQ"], "safe_asset": "SHY",
                                              "_costs": costs},
                          IDX[0].date(), IDX[-1].date(), status="running")  # fmt: skip
        meta = {"cagr": 0.1, "data": {"source": "yfinance", "data_end": "2026-05-01"}}
        complete_run(s, done, meta, eq, dd, sample_ledger())
        running = create_run(s, "combo", {}, date(2020, 1, 2), date(2021, 1, 4), status="running")
    eng.dispose()
    _clear()
    yield TestClient(create_app()), {"done": done, "running": running}
    _clear()


def test_analysis_done(setup: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = setup
    t0 = time.perf_counter()
    r = client.get(f"/api/backtests/{ids['done']}/analysis")
    assert time.perf_counter() - t0 < 1.0
    assert r.status_code == 200
    json.loads(r.text, parse_constant=lambda c: pytest.fail(f"NaN in JSON: {c}"))
    d = r.json()
    assert d["status"] == "done" and d["oos_start"] == "2023-01-01"
    ser = d["series"]
    assert len(ser["dates"]) == 2700 and ser["strategy"][0] == pytest.approx(1.0)
    assert ser["qqq"][0] == pytest.approx(1.0) and ser["spy"][0] == pytest.approx(1.0)
    assert len(d["stats"]) == 10 and d["stats"][0]["qqq"] is not None
    assert len(d["overview"]["checks"]) == 5 and d["overview"]["split"]["oos"]["strategy"]
    assert d["returns"]["monthly"] and d["risk"]["episodes"] and d["risk"]["rolling_beta"]
    params = {p["key"]: p for p in d["params"]}
    assert params["lookback"]["changed"] is True and params["lookback"]["value"] == 99
    assert "_cash" not in params and d["initial_cash"] == 100000
    assert d["repro"]["source"] == "yfinance" and d["repro"]["data_end"] == "2026-05-01"
    cmd = d["repro"]["command"]
    assert cmd.startswith("uv run quant backtest --strategy etf_momentum --start 2016-01-04")
    assert "--param lookback=99" in cmd and "--cash 100000" in cmd and "--slippage 0.0005" in cmd


def test_analysis_running_and_missing(setup: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = setup
    d = client.get(f"/api/backtests/{ids['running']}/analysis").json()
    assert d["status"] == "running" and d["series"] is None and d["stats"] == []
    assert client.get("/api/backtests/99999/analysis").status_code == 404


def test_analysis_without_benchmark_data(
    setup: tuple[TestClient, dict[str, int]], tmp_path: Path
) -> None:
    client, ids = setup
    for p in tmp_path.rglob("symbol=QQQ/data.parquet"):
        p.unlink()
    d = client.get(f"/api/backtests/{ids['done']}/analysis").json()
    assert d["series"]["qqq"] is None and d["stats"][0]["qqq"] is None
    assert d["stats"][0]["value"] is not None and d["series"]["spy"] is not None


def test_command_parses_with_cli(setup: tuple[TestClient, dict[str, int]]) -> None:
    import shlex

    from quant.cli import build_parser

    client, ids = setup
    cmd = client.get(f"/api/backtests/{ids['done']}/analysis").json()["repro"]["command"]
    argv = shlex.split(cmd)
    assert argv[:3] == ["uv", "run", "quant"]
    args = build_parser().parse_args(argv[3:])
    assert args.strategy == "etf_momentum" and dict(args.param)["universe"] == ["SPY", "QQQ"]
    assert dict(args.param)["safe_asset"] == "SHY" and str(args.slippage) == "0.0005"


def test_benchmark_not_covering_run(
    setup: tuple[TestClient, dict[str, int]], tmp_path: Path
) -> None:
    client, ids = setup
    store = ParquetStore(tmp_path)
    df = store.read_bars("QQQ")
    for p in tmp_path.rglob("symbol=QQQ/data.parquet"):
        p.unlink()  # write_bars 会与已有数据合并，先删再写
    store.write_bars("QQQ", df[df.index >= "2019-01-01"], "test")
    d = client.get(f"/api/backtests/{ids['done']}/analysis").json()
    assert d["stats"][1]["qqq"] is None and d["bench_note"] and "QQQ" in d["bench_note"]
    assert d["series"]["qqq"][0] is None and d["series"]["qqq"][-1] is not None


def test_trades_endpoint(setup: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = setup
    d = client.get(f"/api/backtests/{ids['done']}/trades").json()
    assert d["note"] is None and len(d["trades"]) == 4
    sm = d["summary"]
    assert sm["trades"] == 4 and sm["closed"] == 3
    assert sm["win_rate"] == pytest.approx(2 / 3) and sm["profit_factor"] == pytest.approx(205 / 50)
    assert sm["best"]["symbol"] == "AAA" and sm["worst"]["symbol"] == "BBB"
    t1 = d["trades"][0]
    assert t1["days"] == 30 and t1["exit_reason"] == "动量排名跌出前 10"
    reasons = {r["reason"]: r for r in d["by_reason"]}
    assert reasons["跌破支撑线"]["count"] == 2 and reasons["跌破支撑线"]["pnl"] == pytest.approx(
        -45
    )
    by_sym = {r["symbol"]: r for r in d["by_symbol"]}
    assert by_sym["AAA"]["trades"] == 2 and by_sym["AAA"]["pnl"] == pytest.approx(205)


def test_positions_endpoint(setup: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = setup
    d = client.get(f"/api/backtests/{ids['done']}/positions").json()
    assert len(d["daily"]) == 2700 and d["daily"][0]["cash"] == pytest.approx(0.6)
    assert d["daily"][15]["sleeves"] == {"momentum": pytest.approx(0.3)}
    assert d["timing"][0]["days"] == 20
    day = IDX[22].date().isoformat()
    h = client.get(f"/api/backtests/{ids['done']}/positions", params={"date": day}).json()
    assert h["day"] == day and {(x["symbol"], x["sleeve"]) for x in h["holdings"]} == {
        ("AAA", "momentum"),
        ("BBB", "swing"),
    }
    assert (
        d["longest"][0]["symbol"] == "CCC" and d["longest"][0]["days"] == 99
    )  # 仍持有，算到最后一天


def test_analysis_uses_trades(setup: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = setup
    a = client.get(f"/api/backtests/{ids['done']}/analysis").json()
    st = {s["key"]: s for s in a["stats"]}
    assert st["trades"]["value"] == 4 and st["win_rate"]["value"] == pytest.approx(2 / 3)
    sl = a["returns"]["sleeves"]
    assert set(sl["series"]) == {"momentum", "swing"} and sl["series"]["momentum"][
        -1
    ] == pytest.approx(2700 / 100000)
    tm = a["risk"]["timing"]
    assert len(tm) == 1 and tm[0]["days"] == 20 and tm[0]["factor"] == pytest.approx(0.5)


def test_old_run_without_ledger(setup: tuple[TestClient, dict[str, int]], db_url: str) -> None:
    client, _ = setup
    eng = create_engine(db_url)
    eq = prices(3, 0.0005) * 1000
    with Session(eng) as s, s.begin():
        rid = create_run(s, "etf_momentum", {}, IDX[0].date(), IDX[-1].date(), status="running")
        complete_run(s, rid, {"cagr": 0.1}, eq, eq / eq.cummax() - 1)
    eng.dispose()
    t = client.get(f"/api/backtests/{rid}/trades").json()
    assert t["trades"] == [] and "重新运行" in t["note"]
    p = client.get(f"/api/backtests/{rid}/positions").json()
    assert p["daily"] == [] and "重新运行" in p["note"]
    a = client.get(f"/api/backtests/{rid}/analysis").json()
    assert {s["key"]: s for s in a["stats"]}["trades"]["value"] is None
    assert a["returns"]["sleeves"] is None and a["risk"]["timing"] == []
