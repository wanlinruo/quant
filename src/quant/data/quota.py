"""Tiingo 免费档的额度台账（#66）：按美东时间的小时 / 日 / 月计数，持久化到 JSON。

用尽时抛 QuotaExceeded，调用方停止本轮、等下一个窗口；不当作故障告警。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from quant.core.settings import TiingoSettings
from quant.data.providers.base import DataUnavailableError

ET = ZoneInfo("America/New_York")
Window = Literal["hour", "day", "month"]
_WINDOW_TEXT: dict[str, str] = {"hour": "本小时", "day": "今日", "month": "本月"}


class QuotaExceeded(DataUnavailableError):
    def __init__(self, window: Window) -> None:
        super().__init__(f"Tiingo 额度已用尽（{_WINDOW_TEXT[window]}）")
        self.window: Window = window


@dataclass(frozen=True)
class QuotaUsage:
    hour_used: int
    day_used: int
    month: str
    month_symbols: int


def atomic_write_text(path: Path, text: str) -> None:
    """写临时文件后 os.replace，读方不会看到写了一半的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class _State:
    hour: str = ""
    hour_used: int = 0
    hour_exhausted: bool = False
    day: str = ""
    day_used: int = 0
    month: str = ""
    symbols: list[str] = field(default_factory=list)


class QuotaLedger:
    def __init__(
        self,
        path: Path,
        limits: TiingoSettings,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path, self.limits, self._now = path, limits, now
        self._s = (
            _State(**json.loads(path.read_text(encoding="utf-8"))) if path.exists() else _State()
        )

    def _roll(self) -> _State:
        # 每次都以磁盘为准：命令行 sync 与 worker 可能同时使用同一个台账文件
        if self.path.exists():
            self._s = _State(**json.loads(self.path.read_text(encoding="utf-8")))
        t = self._now().astimezone(ET)
        hour, day, month = t.strftime("%Y-%m-%dT%H"), t.strftime("%Y-%m-%d"), t.strftime("%Y-%m")
        s = self._s
        if s.hour != hour:
            s.hour, s.hour_used, s.hour_exhausted = hour, 0, False
        if s.day != day:
            s.day, s.day_used = day, 0
        if s.month != month:
            s.month, s.symbols = month, []
        return s

    def _save(self) -> None:
        atomic_write_text(
            self.path, json.dumps(asdict(self._s), ensure_ascii=False, sort_keys=True)
        )

    def check(self, symbol: str) -> None:
        s = self._roll()
        if s.hour_exhausted or s.hour_used >= self.limits.hourly:
            raise QuotaExceeded("hour")
        if s.day_used >= self.limits.daily:
            raise QuotaExceeded("day")
        if symbol not in s.symbols and len(s.symbols) >= self.limits.monthly_symbols:
            raise QuotaExceeded("month")

    def record(self, symbol: str) -> None:
        s = self._roll()
        s.hour_used += 1
        s.day_used += 1
        if symbol not in s.symbols:
            s.symbols.append(symbol)
        self._save()

    def mark_exhausted(self, window: Window) -> None:
        s = self._roll()
        if window == "hour":
            s.hour_exhausted = True
        elif window == "day":
            s.day_used = self.limits.daily
        self._save()

    def used(self, symbol: str) -> bool:
        """本月是否已请求过该代码（再请求不占“不同标的”额度）。"""
        return symbol in self._roll().symbols

    def month_left(self) -> int:
        return max(0, self.limits.monthly_symbols - len(self._roll().symbols))

    def usage(self) -> QuotaUsage:
        s = self._roll()
        return QuotaUsage(s.hour_used, s.day_used, s.month, len(s.symbols))
