"""组合页接口（#65 Task 5）。需要 QUANT_TEST_DATABASE_URL。"""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, update
from sqlalchemy.orm import Session

from api_fixtures import SEED_FIRST_DAY, SEED_LAST_DAY, needs_db, seed, write_sample_bars
from quant.api.app import create_app
from quant.api.deps import get_engine, get_settings
from quant.data.store import ParquetStore
from quant.db.models import Fill, Order, Signal

pytestmark = [pytest.mark.db, needs_db]

DETAIL: dict[str, Any] = {
    "sleeves": {"momentum": {"AMD": 0.5, "NVDA": 0.5, "META": 0.5}, "swing": {"NVDA": 2 / 3}},
    "sleeve_weights": {"momentum": 0.4, "swing": 0.6},
    "timing_factor": 1.0, "vol_symbol": "QQQ", "vol_20d": 0.22, "vol_threshold": 0.3,
    "momentum_rank": ["AMD", "NVDA", "META"], "universe_size": 503,
    "momentum_rank_asof": ["AMD", "NVDA", *[f"X{i}" for i in range(9)], "META"],
    "rebalance_day": "2026-09-30", "top_n": 10,
}  # fmt: skip
TARGET = {"NVDA": 0.6, "META": 0.2, "AMD": 0.2}
PREV_DETAIL: dict[str, Any] = {**DETAIL, "sleeves": {"momentum": {"META": 1.0}, "swing": {}}}


def _clear() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


def _prepare(eng: Engine, data: Path, bars: tuple[str, ...], detail: bool = True) -> None:
    seed(eng, "paper")
    with Session(eng) as s, s.begin():
        s.execute(
            update(Signal)
            .where(Signal.signal_date == SEED_LAST_DAY)
            .values(target_weights=TARGET, detail=DETAIL if detail else None)
        )
        s.add(Signal(mode="paper", strategy="combo", signal_date=SEED_FIRST_DAY,
                     data_as_of=SEED_FIRST_DAY, target_weights={"META": 0.4},
                     detail=PREV_DETAIL if detail else None))  # fmt: skip
    store = ParquetStore(data)
    for sym in bars:
        write_sample_bars(store, sym)


def _client(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kw: Any) -> TestClient:
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "sim")
    eng = create_engine(db_url)
    _prepare(eng, tmp_path, **kw)
    eng.dispose()
    _clear()
    return TestClient(create_app())


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    yield _client(db_url, monkeypatch, tmp_path, bars=("NVDA", "META", "AMD", "QQQ", "SPY"))
    _clear()


def _by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["symbol"]: r for r in rows}


def test_overview_fields(client: TestClient) -> None:
    o = client.get("/api/portfolio/paper/overview").json()
    assert o["as_of"] == SEED_LAST_DAY.isoformat()
    assert o["net_liquidation"] == 101000 and o["cash"] == 2100
    assert o["summary"]["today_pnl"] == pytest.approx(1000)
    assert o["summary"]["total_return"] == pytest.approx(0.01)
    assert o["risk"]["beta_spy"] is None  # 只有两个快照日
    assert o["top5"] == pytest.approx(98900 / 101000)
    assert o["max_single"] == pytest.approx(60000 / 101000)
    assert o["exposure"] == pytest.approx(98900 / 101000)
    assert o["timing"] == {"factor": 1.0, "vol_symbol": "QQQ", "vol_20d": 0.22, "threshold": 0.3}
    alloc = {a["sleeve"]: a for a in o["allocation"]}
    assert set(alloc) >= {"momentum", "swing", "cash"}
    assert alloc["cash"]["actual"] == pytest.approx(2100 / 101000)
    assert alloc["cash"]["target"] == pytest.approx(0.0)
    assert alloc["swing"]["target"] == pytest.approx(0.4)
    assert o["reconcile"] == {"trading_enabled": False, "last_report_status": None}


def test_performance_normalized_and_rebalance_dates(client: TestClient) -> None:
    p = client.get("/api/portfolio/paper/performance").json()
    pts = p["points"]
    assert [x["date"] for x in pts] == [SEED_FIRST_DAY.isoformat(), SEED_LAST_DAY.isoformat()]
    assert pts[0]["portfolio"] == 1 and pts[0]["qqq"] == 1 and pts[0]["spy"] == 1
    assert pts[1]["portfolio"] == pytest.approx(1.01) and pts[1]["nav"] == 101000
    assert pts[1]["drawdown"] == 0
    assert p["rebalance_dates"] == [SEED_FIRST_DAY.isoformat(), SEED_LAST_DAY.isoformat()]


def test_holdings_with_signal_detail(client: TestClient) -> None:
    rows = _by_symbol(client.get("/api/portfolio/paper/holdings").json())
    nvda, meta = rows["NVDA"], rows["META"]
    assert nvda["sleeve"] == "both" and meta["sleeve"] == "momentum"
    assert nvda["weight"] == pytest.approx(60000 / 101000)
    assert nvda["target_weight"] == pytest.approx(0.6)
    assert meta["deviation"] == pytest.approx(38900 / 101000 - 0.2)
    assert nvda["price"] is not None and nvda["change_pct"] is not None
    assert nvda["unrealized_pnl"] == pytest.approx((nvda["price"] - 130) * 300)
    assert nvda["state"] == "near" and "10 日线" in nvda["state_note"]  # 样本稳步上涨，贴近 10 日线
    # 调仓日排第 3，信号日当天已跌到第 12：下次月度调仓会被换掉
    assert meta["state"] == "exit" and meta["state_note"] == "当日排名 12（调仓日第 3）"
    assert nvda["holding_days"] == 1


def test_holdings_without_signal_detail(
    db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c = _client(db_url, monkeypatch, tmp_path, bars=("NVDA", "META"), detail=False)
    rows = _by_symbol(c.get("/api/portfolio/paper/holdings").json())
    assert rows["NVDA"]["sleeve"] is None and rows["NVDA"]["state"] is None
    o = c.get("/api/portfolio/paper/overview").json()
    assert o["timing"] is None
    _clear()


def test_holdings_missing_price(
    db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c = _client(db_url, monkeypatch, tmp_path, bars=("NVDA",))
    r = c.get("/api/portfolio/paper/holdings")
    assert r.status_code == 200
    meta = _by_symbol(r.json())["META"]
    assert meta["price"] is None and meta["change_pct"] is None and meta["unrealized_pnl"] is None
    assert c.get("/api/portfolio/paper/overview").status_code == 200
    assert c.get("/api/portfolio/paper/performance").json()["points"][0]["qqq"] is None
    _clear()


def test_calendar(client: TestClient) -> None:
    days = client.get("/api/portfolio/paper/calendar", params={"month": "2026-10"}).json()
    assert [d["date"] for d in days] == [SEED_FIRST_DAY.isoformat(), SEED_LAST_DAY.isoformat()]
    assert days[0]["pnl"] is None and days[1]["pnl"] == pytest.approx(1000)
    assert client.get("/api/portfolio/paper/calendar").json() == days  # 缺省为最新快照所在月


def test_calendar_bad_month_is_422(client: TestClient) -> None:
    r = client.get("/api/portfolio/paper/calendar", params={"month": "2026-13"})
    assert r.status_code == 422
    assert any("月份" in d for d in r.json()["detail"])


def test_contributions(client: TestClient) -> None:
    day = client.get("/api/portfolio/paper/contributions", params={"period": "day"}).json()
    # 10-27 只持有 AAPL；10-28 的贡献按前一日持仓计算：AAPL 10 股
    assert {r["symbol"] for r in day} <= {"AAPL"}
    assert (
        client.get("/api/portfolio/paper/contributions", params={"period": "x"}).status_code == 422
    )


def test_rebalance_preview_matches_executor_rules(client: TestClient, tmp_path: Path) -> None:
    from quant.api import analytics as an
    from quant.core.settings import load_settings

    price = {sym: float(ParquetStore(tmp_path).read_bars(sym)["close"].iloc[-1])
             for sym in ("NVDA", "META", "AMD")}  # fmt: skip
    ex = load_settings().executor
    want = an.preview_orders(
        {"NVDA": 300, "META": 60}, TARGET, price, net_liquidation=101000.0,
        cash_buffer=ex.cash_buffer, min_weight_diff=0.04, min_trade_usd=ex.min_trade_usd,
    )  # fmt: skip
    p = client.get("/api/portfolio/paper/rebalance-preview").json()
    assert p["signal_date"] == SEED_LAST_DAY.isoformat()
    rows = _by_symbol(p["rows"])
    assert {(r.symbol, r.side) for r in want} == {(k, v["side"]) for k, v in rows.items()}
    assert rows["AMD"]["side"] == "BUY" and rows["AMD"]["reason"] == "动量排名第 1"
    assert rows["AMD"]["qty"] == next(r.qty for r in want if r.symbol == "AMD")
    notional = sum(r.qty * price[r.symbol] for r in want)
    assert p["turnover"] == pytest.approx(notional / 101000 / 2)
    assert p["est_commission"] > 0


def test_closed_trades_stats(client: TestClient, db_url: str) -> None:
    eng = create_engine(db_url)
    with Session(eng) as s, s.begin():
        s.add_all([
            Order(client_order_id="2026-10-28-combo-NVDA-SELL-1", broker_order_id="3",
                  mode="paper", symbol="NVDA", side="SELL", order_type="LMT", qty=100,
                  limit_price=Decimal("139"), reference_price=Decimal("140"), status="FILLED",
                  filled_qty=100, avg_fill_price=Decimal("139.8")),
            Fill(broker_order_id="3", mode="paper", symbol="NVDA", side="SELL", qty=100,
                 price=Decimal("139.8"), commission=Decimal("1"),
                 filled_at=datetime.now(UTC)),
        ])  # fmt: skip
    eng.dispose()
    out = client.get("/api/portfolio/paper/closed-trades").json()
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["symbol"] == "NVDA" and t["qty"] == 100 and t["pnl"] == pytest.approx(930.0)
    assert out["stats"]["win_rate"] == 1.0
    buy_slip = (130.5 - 130) / 130
    sell_slip = (140 - 139.8) / 140
    assert out["stats"]["avg_slippage"] == pytest.approx((buy_slip + sell_slip) / 2)


def test_portfolio_endpoints_filter_by_mode(client: TestClient) -> None:
    o = client.get("/api/portfolio/live/overview").json()
    assert o["net_liquidation"] is None and o["timing"] is None and o["as_of"] is None
    assert client.get("/api/portfolio/live/holdings").json() == []
    assert client.get("/api/portfolio/live/performance").json() == {
        "points": [], "rebalance_dates": []
    }  # fmt: skip
    assert client.get("/api/portfolio/live/calendar").json() == []
    assert client.get("/api/portfolio/live/contributions").json() == []
    preview = client.get("/api/portfolio/live/rebalance-preview").json()
    assert preview["signal_date"] is None and preview["rows"] == []
    assert client.get("/api/portfolio/live/closed-trades").json()["trades"] == []


def test_signals_latest_by_mode(client: TestClient) -> None:
    paper = client.get("/api/signals/latest", params={"mode": "paper"}).json()
    assert len(paper) == 1 and paper[0]["detail"]["top_n"] == 10
    assert paper[0]["mode"] == "paper"
    assert client.get("/api/signals/latest", params={"mode": "live"}).json() == []
