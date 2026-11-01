"""首页市场看板的预计算（#70）：输入日期 × 代码的宽表，输出可直接序列化的快照。

纯计算：每日任务调用，接口只读结果文件（设计 7.6）。样本不足的指标为 None，不输出 NaN。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from quant.api.analytics import period_returns
from quant.data.market_store import ETF_SET, SECTOR_ETF, GicsTable, MarketStore

YEAR = 252  # 52 周
HISTORY = 260  # 读取的交易日数
STRIP = ("SPY", "QQQ", "DIA", "IWM", "GLD")
INDEX_CANDLES = ("SPY", "QQQ", "DIA", "IWM")
PERIODS = {"1D": 1, "1W": 5, "1M": 21}
MOVER_MIN_CHANGE = 0.02
MOVER_LIMIT = 150
SIGNAL_EXTRA = 100
GAP_PCT = 0.03
VOLUME_SURGE = 2.0
STYLE_PAIRS = (
    ("growth_value", "成长 − 价值", "IWF", "IWD"),
    ("large_small", "大盘 − 小盘", "SPY", "IWM"),
    ("cap_equal", "市值 − 等权", "SPY", "RSP"),
    ("momentum", "动量", "MTUM", "SPY"),
    ("beta_lowvol", "高贝塔 − 低波", "SPHB", "SPLV"),
)
SECTOR_NAME = {sec: ETF_SET[etf][0] for sec, etf in SECTOR_ETF.items()}


def _f(x: object) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _col(df: pd.DataFrame, s: str) -> pd.Series:
    return df[s].dropna() if s in df.columns else pd.Series(dtype=float)


def _ret(s: pd.Series, n: int) -> float | None:
    return _f(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n and s.iloc[-1 - n] else None


def _ytd(s: pd.Series) -> float | None:
    if s.empty:
        return None
    base = s[s.index < pd.Timestamp(year=s.index[-1].year, month=1, day=1)]
    return _f(s.iloc[-1] / base.iloc[-1] - 1) if len(base) and base.iloc[-1] else None


def _spark(s: pd.Series, n: int) -> list[float]:
    return [round(float(x), 4) for x in s.iloc[-n:]]


# ---- 行情条 ----


@dataclass(frozen=True)
class TickerItem:
    symbol: str
    label: str
    close: float | None
    change: float | None
    change_pct: float | None
    spark: list[float]


def ticker_strip(close: pd.DataFrame) -> list[TickerItem]:
    out = []
    for s in STRIP:
        c = _col(close, s)
        last = _f(c.iloc[-1]) if len(c) else None
        prev = _f(c.iloc[-2]) if len(c) > 1 else None
        out.append(TickerItem(
            s, ETF_SET[s][0], last,
            last - prev if last is not None and prev is not None else None,
            _ret(c, 1), _spark(c, 30),
        ))  # fmt: skip
    return out


# ---- 市场宽度 ----


@dataclass(frozen=True)
class Stat:
    value: float | None
    prev: float | None


@dataclass(frozen=True)
class Breadth:
    advancers: Stat
    decliners: Stat
    unchanged: Stat
    new_highs: Stat
    new_lows: Stat
    above_50: Stat
    above_200: Stat
    up_volume_ratio: Stat


def _breadth_at(
    close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, volume: pd.DataFrame, i: int
) -> dict[str, float | None]:
    """第 i 行（负数从末尾数）的宽度；只统计当日与前一日都有收盘价的成分。"""
    c, p = close.iloc[i], close.iloc[i - 1]
    ok = c.notna() & p.notna()
    chg = (c - p)[ok]
    out: dict[str, float | None] = {
        "advancers": float((chg > 0).sum()),
        "decliners": float((chg < 0).sum()),
        "unchanged": float((chg == 0).sum()),
    }
    end = len(close) + i + 1 if i < 0 else i + 1
    prior_h = high.iloc[max(0, end - YEAR) : end - 1]
    prior_l = low.iloc[max(0, end - YEAR) : end - 1]
    enough = prior_h.count() >= YEAR - 1
    # 没有任何成分股满足一年历史时不给（历史补齐前显示“—”，而不是 0）
    out["new_highs"] = (
        float(((high.iloc[i] > prior_h.max()) & enough).sum()) if enough.any() else None
    )
    out["new_lows"] = (
        float(((low.iloc[i] < prior_l.min()) & enough).sum()) if enough.any() else None
    )
    for n in (50, 200):
        ma = close.iloc[max(0, end - n) : end]
        has = ma.count() >= n
        denom = int((has & c.notna()).sum())
        out[f"above_{n}"] = float(((c > ma.mean()) & has).sum()) / denom if denom else None
    v = volume.iloc[i]
    total = float(v[ok].sum())
    out["up_volume_ratio"] = float(v[ok][chg > 0].sum()) / total if total else None
    return out


def breadth(
    close: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    volume: pd.DataFrame,
    members: Sequence[str],
) -> Breadth:
    cols = [s for s in members if s in close.columns]
    frames = [df.reindex(columns=cols) for df in (close, high, low, volume)]
    if len(frames[0]) < 3:
        empty = Stat(None, None)
        return Breadth(*([empty] * 8))
    c, h, lo, v = frames
    today, prev = _breadth_at(c, h, lo, v, -1), _breadth_at(c, h, lo, v, -2)
    return Breadth(**{k: Stat(_f(today[k]), _f(prev[k])) for k in today})


# ---- 风格与板块 ----


@dataclass(frozen=True)
class StylePair:
    key: str
    label: str
    long: str
    short: str
    d1: float | None
    m1: float | None


def styles(close: pd.DataFrame) -> list[StylePair]:
    out = []
    for key, label, a, b_ in STYLE_PAIRS:
        ca, cb = _col(close, a), _col(close, b_)

        def diff(n: int, ca: pd.Series = ca, cb: pd.Series = cb) -> float | None:
            x, y = _ret(ca, n), _ret(cb, n)
            return _f(x - y) if x is not None and y is not None else None

        out.append(StylePair(key, label, a, b_, diff(1), diff(PERIODS["1M"])))
    return out


@dataclass(frozen=True)
class SectorRow:
    symbol: str
    name: str
    d1: float | None
    w1: float | None
    m1: float | None
    ytd: float | None
    rel_spy_d1: float | None
    spark: list[float]


def sectors(close: pd.DataFrame) -> list[SectorRow]:
    spy_d1 = _ret(_col(close, "SPY"), 1)
    rows = []
    for etf in SECTOR_ETF.values():
        c = _col(close, etf)
        d1 = _ret(c, 1)
        rows.append(SectorRow(
            etf, ETF_SET[etf][0], d1, _ret(c, 5), _ret(c, 21), _ytd(c),
            _f(d1 - spy_d1) if d1 is not None and spy_d1 is not None else None, _spark(c, 21),
        ))  # fmt: skip
    return sorted(rows, key=lambda r: -(r.d1 if r.d1 is not None else -math.inf))


def sector_periods(close: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    out = {}
    for etf in SECTOR_ETF.values():
        c = _col(close, etf).dropna()
        if len(c):
            out[etf] = period_returns(c, c.index[-1].date())
    return out


# ---- 异动个股 ----


@dataclass(frozen=True)
class Mover:
    symbol: str
    name: str | None
    sector: str | None
    market_cap: float | None
    close: float
    change_pct: float
    volume_ratio: float | None
    signals: list[str]
    spark: list[float]
    held: bool = False  # 持仓标的：不受涨跌幅门槛与条数上限限制，供“我的持仓”筛选


def _signals(
    c: pd.Series, o: pd.Series, h: pd.Series, lo: pd.Series, v: pd.Series
) -> tuple[list[str], float | None]:
    out: list[str] = []
    vr = _f(v.iloc[-1] / v.iloc[-21:-1].mean()) if len(v) > 20 and v.iloc[-21:-1].mean() else None
    if vr is not None and vr >= VOLUME_SURGE and c.iloc[-1] >= c.iloc[-20:].max():
        out.append("放量突破")
    if len(c) > 1 and len(o) and c.iloc[-2] and abs(o.iloc[-1] / c.iloc[-2] - 1) >= GAP_PCT:
        out.append("跳空")
    if len(h) >= YEAR and h.iloc[-1] > h.iloc[-YEAR:-1].max():
        out.append("52周新高")
    if len(lo) >= YEAR and lo.iloc[-1] < lo.iloc[-YEAR:-1].min():
        out.append("52周新低")
    if len(c) > 50:
        ma, ma_prev = c.iloc[-50:].mean(), c.iloc[-51:-1].mean()
        if c.iloc[-1] < ma and c.iloc[-2] >= ma_prev:
            out.append("跌破50日线")
    return out, vr


def movers(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    volume: pd.DataFrame,
    gics: GicsTable,
    caps: Mapping[str, float | None],
    held: Iterable[str] = (),
) -> list[Mover]:
    hold = set(held)
    out: list[Mover] = []
    mine: list[Mover] = []
    for s in [*gics.members, *sorted(hold - set(gics.members))]:
        c = _col(close, s)
        if len(c) < 2 or c.index[-1] != close.index[-1]:
            continue  # 当日没有数据
        chg = _ret(c, 1)
        if chg is None:
            continue
        sig, vr = _signals(c, _col(open_, s), _col(high, s), _col(low, s), _col(volume, s))
        if abs(chg) < MOVER_MIN_CHANGE and not sig and s not in hold:
            continue
        sector = gics.sector(s)
        name = gics.name(s) or (ETF_SET[s][0] if s in ETF_SET else None)
        (mine if s in hold else out).append(Mover(
            s, name, SECTOR_NAME.get(sector or "", sector), _f(caps.get(s)),
            float(c.iloc[-1]), chg, vr, sig, _spark(c, 20), s in hold,
        ))  # fmt: skip
    out.sort(key=lambda m: -abs(m.change_pct))
    # 涨跌幅前 MOVER_LIMIT 只之外，有信号的再并入（最多 SIGNAL_EXTRA 只），避免信号股被截掉
    extra = [m for m in out[MOVER_LIMIT:] if m.signals][:SIGNAL_EXTRA]
    return sorted([*out[:MOVER_LIMIT], *extra, *mine], key=lambda m: -abs(m.change_pct))


# ---- 热力图与指数日线 ----


@dataclass(frozen=True)
class HeatCell:
    symbol: str
    name: str | None
    sector: str | None
    market_cap: float
    ret: float | None


def heatmap(
    close: pd.DataFrame, gics: GicsTable, caps: Mapping[str, float | None]
) -> dict[str, list[HeatCell]]:
    out: dict[str, list[HeatCell]] = {k: [] for k in (*PERIODS, "YTD")}
    for s in gics.members:
        cap = _f(caps.get(s))
        c = _col(close, s)
        if cap is None or c.empty:
            continue
        sector = gics.sector(s)
        name = SECTOR_NAME.get(sector or "", sector)
        fresh = c.index[-1] == close.index[-1]  # 当日没有数据的不显示几天前的涨跌
        for k, n in PERIODS.items():
            out[k].append(HeatCell(s, gics.name(s), name, cap, _ret(c, n) if fresh else None))
        out["YTD"].append(HeatCell(s, gics.name(s), name, cap, _ytd(c) if fresh else None))
    return out


@dataclass(frozen=True)
class Candle:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def index_candles(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    volume: pd.DataFrame,
    symbols: Sequence[str] = INDEX_CANDLES,
    days: int = YEAR,
) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    for s in symbols:
        if s not in close.columns:
            out[s] = []
            continue
        df = (
            pd.DataFrame({"o": open_[s], "h": high[s], "l": low[s], "c": close[s], "v": volume[s]})
            .dropna()
            .iloc[-days:]
        )
        out[s] = [
            Candle(t.date().isoformat(), float(o), float(h), float(lo), float(c), float(v))
            for t, o, h, lo, c, v in zip(
                pd.DatetimeIndex(df.index), df["o"], df["h"], df["l"], df["c"], df["v"],
                strict=True,
            )
        ]  # fmt: skip
    return out


# ---- 今日要点 ----


def highlights(
    strip: Sequence[TickerItem], secs: Sequence[SectorRow], br: Breadth, close: pd.DataFrame
) -> str:
    parts: list[str] = []
    spy = next((x for x in strip if x.symbol == "SPY"), None)
    c = _col(close, "SPY")
    if spy and spy.change_pct is not None and spy.close is not None and len(c):
        head = f"标普 500（SPY）{spy.change_pct:+.2%} 收于 {spy.close:,.2f}"
        if len(c) >= YEAR:  # 历史不足一年时不写“52 周”
            head += f"，距 52 周高点 {spy.close / c.iloc[-YEAR:].max() - 1:+.1%}"
        parts.append(head)
    ranked = [s for s in secs if s.d1 is not None]
    if len(ranked) >= 2:
        parts.append(f"{ranked[0].name}领涨、{ranked[-1].name}领跌")
    total = sum(x for x in (br.advancers.value, br.decliners.value, br.unchanged.value) if x)
    if total and br.advancers.value is not None:
        parts.append(f"上涨家数 {br.advancers.value / total:.0%}")
    if br.new_highs.value is not None and br.new_lows.value is not None:
        parts.append(f"52 周新高 {br.new_highs.value:.0f} 只、新低 {br.new_lows.value:.0f} 只")
    return "；".join(parts) + "。" if parts else ""


# ---- 快照 ----


@dataclass(frozen=True)
class MarketSnapshot:
    as_of: str
    stale: bool
    generated_at: str
    members: int
    coverage: float | None
    strip: list[TickerItem]
    highlights: str
    breadth: Breadth
    styles: list[StylePair]
    sectors: list[SectorRow]
    movers: list[Mover]
    heatmap: dict[str, list[HeatCell]]
    index_candles: dict[str, list[Candle]] = field(default_factory=dict)
    # 板块 ETF 的区间收益（口径同个股页 period_returns），个股页“板块 ETF”行用（#70 第 3 批）
    sector_periods: dict[str, dict[str, float | None]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = _clean(asdict(self))
        return out


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    return x


def build_snapshot(
    store: MarketStore, gics: GicsTable, as_of: date, stale: bool, held: Iterable[str] = ()
) -> MarketSnapshot:
    p = {f: store.panel(f, last_n=HISTORY) for f in ("close", "open", "high", "low", "volume")}
    close = p["close"]
    caps = {k: v.market_cap for k, v in store.caps().items()}
    members = gics.members
    if close.empty:
        empty = Stat(None, None)
        now = datetime.now(UTC).isoformat()
        return MarketSnapshot(as_of.isoformat(), stale, now, len(members), None, [], "",
                              Breadth(*([empty] * 8)), [], [], [], {}, {})  # fmt: skip
    last = close.iloc[-1]
    have = sum(1 for s in members if s in last.index and pd.notna(last[s]))
    strip = ticker_strip(close)
    br = breadth(close, p["high"], p["low"], p["volume"], members)
    secs = sectors(close)
    return MarketSnapshot(
        as_of=close.index[-1].date().isoformat(),
        stale=stale,
        generated_at=datetime.now(UTC).isoformat(),
        members=len(members),
        coverage=have / len(members) if members else None,
        strip=strip,
        highlights=highlights(strip, secs, br, close),
        breadth=br,
        styles=styles(close),
        sectors=secs,
        movers=movers(close, p["open"], p["high"], p["low"], p["volume"], gics, caps, held),
        heatmap=heatmap(close, gics, caps),
        index_candles=index_candles(close, p["open"], p["high"], p["low"], p["volume"]),
        sector_periods=sector_periods(close),
    )
