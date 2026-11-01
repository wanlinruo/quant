"""Forex Factory 每周经济日历 JSON（#69 第 2 批）：非官方接口、无需 key，只有本周、没有实际值。

个人使用，每天请求 1 次。只返回美国（USD）的事件，时间换算为美东。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from quant.data.providers.base import DataUnavailableError
from quant.data.providers.http import Fetch, http_fetch

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
ET = ZoneInfo("America/New_York")
IMPACT = {"High": 3, "Medium": 2, "Low": 1}


@dataclass(frozen=True)
class FfEvent:
    title: str
    time: datetime  # 美东
    impact: int  # 3 高 / 2 中 / 1 低 / 0 假日等
    forecast: str
    previous: str


def this_week(fetch: Fetch = http_fetch) -> list[FfEvent]:
    try:
        status, body = fetch(URL, {"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        raise DataUnavailableError(f"Forex Factory 请求失败（{type(e).__name__}）") from None
    if status != 200 or not isinstance(body, list):
        raise DataUnavailableError(f"Forex Factory 返回 {status}（格式不符）")
    out = []
    for r in body:
        try:
            if r.get("country") != "USD":
                continue
            t = datetime.fromisoformat(str(r["date"])).astimezone(ET)
            impact = IMPACT.get(str(r.get("impact")), 0)
            fc, prev = str(r.get("forecast") or ""), str(r.get("previous") or "")
            out.append(FfEvent(str(r["title"]), t, impact, fc, prev))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out
