"""首页财经日历（#69 第 2 批）：经济数据（Forex Factory + FRED 实际值）、财报（Finnhub）、FOMC。

纯计算。FF 只给本周、没有实际值：事件累积存盘（merge_ff），实际值只为 ACTUALS 中的事件从 FRED 取。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quant.data.providers.finnhub import EarningsRaw
from quant.data.providers.forexfactory import FfEvent
from quant.data.providers.fred import Obs

ET = ZoneInfo("America/New_York")
KEEP_DAYS = 14  # 累积的 FF 事件保留天数
UPCOMING_DAYS = 7
FOMC_DAYS = 14
GROUP_MAX = 4  # 财报合并行最多列出几只

# FF 标题 → （FRED 序列, 计算方式, 格式）
ACTUALS: dict[str, tuple[str, str, str]] = {
    "Non-Farm Employment Change": ("PAYEMS", "diff", "{:.0f}K"),
    "Unemployment Rate": ("UNRATE", "level", "{:.1f}%"),
    "Average Hourly Earnings m/m": ("CES0500000003", "pct", "{:.1f}%"),
    "Unemployment Claims": ("ICSA", "k", "{:.0f}K"),
    "JOLTS Job Openings": ("JTSJOL", "m", "{:.2f}M"),
    "CPI m/m": ("CPIAUCSL", "pct", "{:.1f}%"),
    "Core CPI m/m": ("CPILFESL", "pct", "{:.1f}%"),
    "Core PCE Price Index m/m": ("PCEPILFE", "pct", "{:.1f}%"),
    "Retail Sales m/m": ("RSAFS", "pct", "{:.1f}%"),
}
SESSION = {"bmo": ("盘前", time(8, 0)), "dmh": ("盘中", time(12, 0)), "amc": ("盘后", time(16, 5))}
# FF 中与 FOMC 决议同一时刻的事件：并入自生成的“FOMC 利率决议”一行，不重复显示
FOMC_FF_TITLES = {"Federal Funds Rate", "FOMC Statement", "FOMC Press Conference",
                  "FOMC Economic Projections"}  # fmt: skip


@dataclass(frozen=True)
class CalItem:
    time: str  # 美东 ISO
    date_only: bool  # 时间未定，只有日期
    kind: str  # "econ" | "earnings" | "fomc"
    title: str
    impact: int  # 1–3
    actual: str | None
    forecast: str | None
    previous: str | None
    symbols: list[str]
    held: bool
    session: str | None  # "盘前" | "盘后" | None


def fred_actual(
    title: str, event_day: date, obs: Sequence[Obs], updated: datetime | None
) -> str | None:
    """事件当天 FRED 已更新时，按 ACTUALS 计算实际值；否则 None（不拿上一期冒充）。"""
    spec = ACTUALS.get(title)
    if spec is None or updated is None or not obs:
        return None
    if updated.astimezone(ET).date() < event_day:
        return None
    _, how, fmt = spec
    pts = sorted(obs, key=lambda o: o.date)
    last = pts[-1].value
    if how in ("diff", "pct"):
        if len(pts) < 2:
            return None
        prev = pts[-2].value
        if how == "diff":
            v = last - prev
        else:
            if not prev:
                return None
            v = (last / prev - 1) * 100
    elif how == "k" or how == "m":
        v = last / 1000
    else:
        v = last
    return fmt.format(v)


def merge_ff(
    old: Sequence[Mapping[str, Any]], new: Sequence[FfEvent], today: date
) -> list[dict[str, Any]]:
    """累积 FF 事件（只收高 / 中重要性），title + 日期去重、后取的覆盖，丢弃 14 天前的。"""
    keep: dict[tuple[str, str], dict[str, Any]] = {}
    for e in old:
        keep[(str(e["title"]), str(e["time"])[:10])] = dict(e)
    for f in new:
        if f.impact < 2:
            continue
        t = f.time.astimezone(ET)
        keep[(f.title, t.date().isoformat())] = {"title": f.title, "time": t.isoformat(),
                                                 "impact": f.impact, "forecast": f.forecast,
                                                 "previous": f.previous}  # fmt: skip
    cutoff = today - timedelta(days=KEEP_DAYS)
    out = [e for e in keep.values() if datetime.fromisoformat(e["time"]).date() >= cutoff]
    return sorted(out, key=lambda e: datetime.fromisoformat(e["time"]))


def _num(v: float | None) -> str | None:
    return None if v is None else f"{round(v, 2):g}"


def _earnings_items(
    earnings: Sequence[EarningsRaw], holdings: set[str], big: set[str]
) -> list[tuple[datetime, CalItem]]:
    groups: dict[tuple[date, str], list[EarningsRaw]] = defaultdict(list)
    seen: set[tuple[str, date]] = set()
    for e in earnings:
        if (e.symbol not in holdings and e.symbol not in big) or (e.symbol, e.date) in seen:
            continue
        seen.add((e.symbol, e.date))
        hour = e.hour if e.hour in SESSION else ""
        groups[(e.date, hour)].append(e)
    out = []
    for (d, hour), rows in groups.items():
        rows.sort(key=lambda e: (e.symbol not in holdings, e.symbol))
        syms = [e.symbol for e in rows]
        if len(syms) > GROUP_MAX:
            title = f"{' · '.join(syms[:GROUP_MAX])} 等 {len(syms)} 只财报"
        else:
            title = f"{' · '.join(syms)} 财报"
        session, at = SESSION.get(hour, (None, time(12, 0)))
        t = datetime.combine(d, at, ET)
        held = any(s in holdings for s in syms)
        first = rows[0]
        out.append((t, CalItem(t.isoformat(), session is None, "earnings", title, 3 if held else 2,
                               _num(first.eps_actual), _num(first.eps_estimate), None, syms,
                               held, session)))  # fmt: skip
    return out


def build_calendar(
    ff: Sequence[Mapping[str, Any]],
    actuals: Mapping[str, str | None],
    earnings: Sequence[EarningsRaw],
    fomc: Sequence[date],
    holdings: set[str],
    big: set[str],
    now: datetime,
    prev_day: date,
) -> tuple[list[CalItem], list[CalItem]]:
    """返回（已公布，按时间降序；接下来，按时间升序）。"""
    start = datetime.combine(prev_day, time(0), ET)
    items: list[tuple[datetime, CalItem]] = []
    local_fomc_days = set(fomc)
    rate: dict[date, Mapping[str, Any]] = {}  # FF 的 Federal Funds Rate：预期 / 前值
    ff_fomc_days: set[date] = set()
    for e in ff:
        t = datetime.fromisoformat(str(e["time"])).astimezone(ET)
        if e["title"] in FOMC_FF_TITLES:
            ff_fomc_days.add(t.date())
            if e["title"] == "Federal Funds Rate":
                rate[t.date()] = e
            continue
        key = f"{e['title']}|{t.date().isoformat()}"
        items.append((t, CalItem(t.isoformat(), False, "econ", str(e["title"]), int(e["impact"]),
                                 actuals.get(key), e.get("forecast") or None,
                                 e.get("previous") or None, [], False, None)))  # fmt: skip
    items += _earnings_items(earnings, holdings, big)
    # FF 是实际发布时间；若本地维护日期相差一天，以 FF 为准并合并，避免出现相邻两条。
    reconciled_local = {
        d
        for d in local_fomc_days
        if not any(abs((d - ff_day).days) <= 1 for ff_day in ff_fomc_days)
    }
    fomc_days = reconciled_local | ff_fomc_days
    fomc_items = []
    for d in sorted(fomc_days):
        t = datetime.combine(d, time(14, 0), ET)
        r = rate.get(d, {})
        fomc_items.append((t, CalItem(t.isoformat(), False, "fomc", "FOMC 利率决议", 3,
                                      actuals.get(f"Federal Funds Rate|{d.isoformat()}"),
                                      r.get("forecast") or None, r.get("previous") or None,
                                      [], False, None)))  # fmt: skip
    released = [(t, i) for t, i in [*items, *fomc_items] if start <= t <= now]
    upcoming = [(t, i) for t, i in items if now < t <= now + timedelta(days=UPCOMING_DAYS)]
    upcoming += [(t, i) for t, i in fomc_items if now < t <= now + timedelta(days=FOMC_DAYS)]
    released.sort(key=lambda x: x[0], reverse=True)
    upcoming.sort(key=lambda x: x[0])
    return [i for _, i in released], [i for _, i in upcoming]
