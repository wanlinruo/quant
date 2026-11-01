"""Tiingo 免费档额度台账（#66 Task 1）。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant.core.settings import TiingoSettings
from quant.data.quota import QuotaExceeded, QuotaLedger

LIMITS = TiingoSettings(hourly=50, daily=1000, monthly_symbols=500)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 2, 15, 5, tzinfo=UTC)  # 美东 10:05

    def __call__(self) -> datetime:
        return self.t


def test_ledger_limits(tmp_path: Path) -> None:
    clock = Clock()
    led = QuotaLedger(tmp_path / "q.json", LIMITS, clock)
    for i in range(50):
        led.check(f"S{i}")
        led.record(f"S{i}")
    with pytest.raises(QuotaExceeded) as e:
        led.check("S0")
    assert e.value.window == "hour"
    clock.t += timedelta(hours=1)
    led.check("S0")  # 新的一小时恢复
    assert led.usage().hour_used == 0 and led.usage().day_used == 50


def test_ledger_monthly_symbols(tmp_path: Path) -> None:
    led = QuotaLedger(
        tmp_path / "q.json", TiingoSettings(hourly=10_000, daily=10_000, monthly_symbols=3), Clock()
    )
    for s in ("A", "B", "C"):
        led.record(s)
    led.check("A")  # 已用过的代码不占新额度
    with pytest.raises(QuotaExceeded) as e:
        led.check("D")
    assert e.value.window == "month"


def test_ledger_daily_and_new_month(tmp_path: Path) -> None:
    clock = Clock()
    led = QuotaLedger(
        tmp_path / "q.json", TiingoSettings(hourly=10_000, daily=2, monthly_symbols=1), clock
    )
    led.record("A")
    led.record("A")
    with pytest.raises(QuotaExceeded) as e:
        led.check("A")
    assert e.value.window == "day"
    clock.t = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)
    led.check("B")  # 新的一月、新的一天
    assert led.usage().month == "2026-12" and led.usage().month_symbols == 0


def test_mark_exhausted_hour(tmp_path: Path) -> None:
    clock = Clock()
    led = QuotaLedger(tmp_path / "q.json", LIMITS, clock)
    led.mark_exhausted("hour")
    with pytest.raises(QuotaExceeded):
        led.check("A")
    clock.t += timedelta(hours=1)
    led.check("A")


def test_ledger_file_survives_restart(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "state" / "q.json"
    a = QuotaLedger(path, LIMITS, clock)
    a.record("A")
    a.record("B")
    b = QuotaLedger(path, LIMITS, clock)
    assert b.usage().hour_used == 2 and b.usage().month_symbols == 2


def test_two_ledgers_share_file(tmp_path: Path) -> None:
    """命令行 sync 与 worker 各持一个台账对象：每次操作都以磁盘为准，不互相覆盖。"""
    clock = Clock()
    a = QuotaLedger(tmp_path / "q.json", LIMITS, clock)
    b = QuotaLedger(tmp_path / "q.json", LIMITS, clock)
    a.record("A")
    b.record("B")
    a.record("C")
    assert QuotaLedger(tmp_path / "q.json", LIMITS, clock).usage().hour_used == 3
    assert b.usage().month_symbols == 3
