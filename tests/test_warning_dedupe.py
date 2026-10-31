"""每日数据提示去重（#56）：同一条提示 7 天内只发一次。"""

from datetime import date, timedelta
from pathlib import Path

from quant.jobs.daily import RESEND_DAYS, fresh_warnings

D = date(2026, 11, 2)


def test_same_warning_not_repeated_daily(tmp_path: Path) -> None:
    path = tmp_path / "state" / "warnings.json"
    assert fresh_warnings(path, ["A", "B"], D) == ["A", "B"]
    assert fresh_warnings(path, ["A", "B"], D + timedelta(days=1)) == []
    assert fresh_warnings(path, ["A", "C"], D + timedelta(days=2)) == ["C"]  # 新问题照发


def test_resent_after_interval(tmp_path: Path) -> None:
    path = tmp_path / "warnings.json"
    fresh_warnings(path, ["A"], D)
    assert fresh_warnings(path, ["A"], D + timedelta(days=RESEND_DAYS - 1)) == []
    assert fresh_warnings(path, ["A"], D + timedelta(days=RESEND_DAYS)) == ["A"]


def test_rerun_same_day_sends_again(tmp_path: Path) -> None:
    # 同一交易日补跑（--rerun-failed）时照常提示：人工补跑的人需要看到
    path = tmp_path / "warnings.json"
    fresh_warnings(path, ["A"], D)
    assert fresh_warnings(path, ["A"], D) == ["A"]


def test_corrupt_state_sends_all(tmp_path: Path) -> None:
    path = tmp_path / "warnings.json"
    path.write_text("{not json", encoding="utf-8")
    assert fresh_warnings(path, ["A"], D) == ["A"]
