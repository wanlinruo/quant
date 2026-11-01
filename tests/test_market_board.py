"""首页市场看板的预计算（#70 第 1 批 Task 3）。合成数据，数值手算。"""

import json
import math

import numpy as np
import pandas as pd
import pytest

from quant.data.market_store import ETF_SET, SECTOR_ETF, GicsTable
from quant.market import board as b

N = 300
IDX = pd.bdate_range(end="2026-10-30", periods=N)
GICS = GicsTable({
    "UP": ("Up Corp", "Information Technology"), "DOWN": ("Down Inc", "Energy"),
    "FLAT": ("Flat Co", "Utilities"), "GAP": ("Gap Ltd", "Financials"),
    "BRK": ("Breakout", "Industrials"), "NEW": ("New Listing", "Health Care"),
})  # fmt: skip


def frames() -> dict[str, pd.DataFrame]:
    t = np.arange(N, dtype=float)
    close = pd.DataFrame(index=IDX)
    for i, s in enumerate(ETF_SET):
        close[s] = 100 * (1 + 0.0005 * (i + 1)) ** t
    close["UP"] = 100 + t  # 每天 +1，持续创新高
    close["DOWN"] = 400 - t
    close["FLAT"] = 50.0
    close["GAP"] = 100.0
    close.loc[IDX[-1], "GAP"] = 110.0  # 当日跳空 +10%
    brk = np.full(N, 80.0)
    brk[-1] = 81.0  # 20 日最高 + 放量
    close["BRK"] = brk
    close["NEW"] = np.nan
    close.loc[IDX[-30:], "NEW"] = 20 + t[-30:] * 0
    open_ = close.copy()
    open_.loc[IDX[-1], "GAP"] = 110.0
    high, low = close + 0.5, close - 0.5
    volume = close * 0 + 1000.0
    volume.loc[IDX[-1], "BRK"] = 5000.0
    volume.loc[IDX[-1], "UP"] = 3000.0
    return {"close": close, "open": open_, "high": high, "low": low, "volume": volume}


def test_ticker_strip_change_and_spark() -> None:
    f = frames()
    strip = b.ticker_strip(f["close"])
    assert [x.symbol for x in strip] == ["SPY", "QQQ", "DIA", "IWM", "GLD"]
    spy = strip[0]
    c = f["close"]["SPY"]
    assert spy.label == "标普 500" and spy.close == pytest.approx(c.iloc[-1])
    assert spy.change == pytest.approx(c.iloc[-1] - c.iloc[-2])
    assert spy.change_pct == pytest.approx(c.iloc[-1] / c.iloc[-2] - 1)
    assert len(spy.spark) == 30


def test_breadth_counts_and_prev() -> None:
    f = frames()
    br = b.breadth(f["close"], f["high"], f["low"], f["volume"], GICS.members)
    # 当日：UP、GAP、BRK 上涨；DOWN 下跌；FLAT、NEW 持平
    assert (br.advancers.value, br.decliners.value, br.unchanged.value) == (3, 1, 2)
    assert (br.advancers.prev, br.decliners.prev, br.unchanged.prev) == (1, 1, 4)
    assert br.new_highs.value == 3  # UP、GAP、BRK 收盘 ≥ 252 日最高价
    assert br.new_lows.value == 1  # DOWN
    up_vol = 3000 + 1000 + 5000
    assert br.up_volume_ratio.value == pytest.approx(up_vol / (up_vol + 1000 + 1000 + 1000))


def test_breadth_skips_short_history() -> None:
    f = frames()
    br = b.breadth(f["close"], f["high"], f["low"], f["volume"], GICS.members)
    # 200 日线：NEW 只有 30 天，不计入分母；UP、GAP、BRK 在线上，DOWN、FLAT 不在
    assert br.above_200.value == pytest.approx(3 / 5)


def test_styles_pairs() -> None:
    c = frames()["close"]
    s = {x.key: x for x in b.styles(c)}
    assert set(s) == {"growth_value", "large_small", "cap_equal", "momentum", "beta_lowvol"}

    def ret(x: str, n: int) -> float:
        return float(c[x].iloc[-1] / c[x].iloc[-1 - n] - 1)

    assert s["growth_value"].d1 == pytest.approx(ret("IWF", 1) - ret("IWD", 1))
    assert s["momentum"].m1 == pytest.approx(ret("MTUM", 21) - ret("SPY", 21))


def test_sectors_sorted_and_rel() -> None:
    c = frames()["close"]
    rows = b.sectors(c)
    first, last = rows[0].d1, rows[-1].d1
    assert len(rows) == 11 and first is not None and last is not None and first >= last
    xlk = next(r for r in rows if r.symbol == "XLK")
    assert xlk.name == "信息技术"
    spy_d1 = c["SPY"].iloc[-1] / c["SPY"].iloc[-2] - 1
    assert xlk.rel_spy_d1 == pytest.approx(xlk.d1 - spy_d1)
    ytd_base = c["XLK"][c.index <= pd.Timestamp("2025-12-31")].iloc[-1]
    assert xlk.ytd == pytest.approx(c["XLK"].iloc[-1] / ytd_base - 1)


def test_movers_signals() -> None:
    f = frames()
    caps = {"UP": 3e9, "GAP": 2e9}
    ms = {
        m.symbol: m
        for m in b.movers(f["close"], f["open"], f["high"], f["low"], f["volume"], GICS, caps)
    }
    assert "跳空" in ms["GAP"].signals and ms["GAP"].change_pct == pytest.approx(0.10)
    assert "放量突破" in ms["BRK"].signals and ms["BRK"].volume_ratio == pytest.approx(5.0)
    assert "52周新高" in ms["UP"].signals and "52周新低" in ms["DOWN"].signals
    assert "FLAT" not in ms and "NEW" not in ms
    assert (
        ms["GAP"].sector == "金融" and ms["GAP"].name == "Gap Ltd" and ms["GAP"].market_cap == 2e9
    )
    order = [
        m.symbol
        for m in b.movers(f["close"], f["open"], f["high"], f["low"], f["volume"], GICS, caps)
    ]
    assert order[0] == "GAP"  # 按 |涨跌| 排序


def test_breaks_ma50() -> None:
    c = pd.DataFrame(
        {"X": np.r_[np.full(80, 100.0), 90.0]}, index=pd.bdate_range(end="2026-10-30", periods=81)
    )
    g = GicsTable({"X": ("X Co", "Energy")})
    m = b.movers(c, c, c + 1, c - 1, c * 0 + 1000.0, g, {})
    assert "跌破50日线" in m[0].signals


def test_heatmap_skips_missing_cap() -> None:
    f = frames()
    hm = b.heatmap(f["close"], GICS, {"UP": 3e9, "DOWN": 1e9})
    assert set(hm) == {"1D", "1W", "1M", "YTD"}
    cells = {c.symbol: c for c in hm["1D"]}
    assert set(cells) == {"UP", "DOWN"} and cells["UP"].sector == "信息技术"
    assert cells["UP"].ret == pytest.approx(
        f["close"]["UP"].iloc[-1] / f["close"]["UP"].iloc[-2] - 1
    )


def test_highlights_text() -> None:
    f = frames()
    strip = b.ticker_strip(f["close"])
    secs = b.sectors(f["close"])
    br = b.breadth(f["close"], f["high"], f["low"], f["volume"], GICS.members)
    text = b.highlights(strip, secs, br, f["close"])
    spy = f["close"]["SPY"]
    d1 = spy.iloc[-1] / spy.iloc[-2] - 1
    assert text == (
        f"标普 500（SPY）{d1:+.2%} 收于 {spy.iloc[-1]:,.2f}，距 52 周高点 {0:+.1%}；"
        f"{secs[0].name}领涨、{secs[-1].name}领跌；上涨家数 50%；52 周新高 3 只、新低 1 只。"
    )


def test_snapshot_json_serializable_no_nan(tmp_path: object) -> None:
    from pathlib import Path

    from quant.data.market_store import MarketStore

    root = Path(str(tmp_path))
    store = MarketStore(root)
    f = frames()
    for d in IDX:
        day = pd.DataFrame({k: f[k].loc[d] for k in ("open", "high", "low", "close", "volume")})
        day = day.rename_axis("symbol").reset_index()
        day["vwap"] = day["close"]
        store.write_day(d.date(), day, keep=set(day["symbol"]))
    snap = b.build_snapshot(store, GICS, IDX[-1].date(), stale=False)
    doc = snap.to_json()
    text = json.dumps(doc, allow_nan=False)
    assert doc["as_of"] == "2026-10-30" and doc["stale"] is False
    assert doc["coverage"] == pytest.approx(1.0)
    assert set(doc["index_candles"]) == {"SPY", "QQQ", "DIA", "IWM"}
    assert len(doc["index_candles"]["SPY"]) == 252
    assert not math.isnan(doc["breadth"]["above_50"]["value"])
    assert "NaN" not in text
    xlk = doc["sector_periods"]["XLK"]
    assert list(xlk) == ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y"]
    d1 = next(r["d1"] for r in doc["sectors"] if r["symbol"] == "XLK")
    assert xlk["1D"] == pytest.approx(d1) and xlk["6M"] is not None
    assert xlk["1Y"] is None  # 合成数据 260 个工作日只跨 361 天，不足 1 年时不给
    assert set(doc["sector_periods"]) == set(SECTOR_ETF.values())
