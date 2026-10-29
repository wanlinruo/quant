"""新浪美股日 K：用固定响应文本测试解析与重试，不访问网络。"""

from datetime import date

import pytest

from quant.data.providers.base import BAR_COLUMNS, DataUnavailableError
from quant.data.providers.sina import SinaProvider, parse

TEXT = (
    "/*<script>location.href='//sina.com';</script>*/\n"
    'var([{"d":"2026-10-26","o":"762.25","h":"767.73","l":"761.63","c":"765.37",'
    '"v":"31737797","a":"24283500000"},'
    '{"d":"2026-10-27","o":"767.34","h":"771.14","l":"765.21","c":"769.66","v":"28631586","a":"0"},'
    '{"d":"2026-10-28","o":"771.43","h":"773.36","l":"769.24","c":"772.31","v":"30133804","a":"0"}]);'
)


def test_parse_and_range() -> None:
    p = SinaProvider(fetch=lambda s: TEXT, sleep=lambda s: None)
    bars = p.daily_bars("SPY", date(2026, 10, 27), date(2026, 10, 28))
    assert list(bars.columns) == list(BAR_COLUMNS)
    assert [d.date() for d in bars.index] == [date(2026, 10, 27), date(2026, 10, 28)]
    assert bars["close"].tolist() == [769.66, 772.31]
    assert bars["adj_close"].isna().all()
    assert bars["volume"].dtype == "int64"


def test_parse_errors() -> None:
    with pytest.raises(DataUnavailableError):
        parse("<html>blocked</html>")
    with pytest.raises(DataUnavailableError):
        parse("var([]);")


def test_retry_then_success() -> None:
    calls: list[str] = []

    def flaky(symbol: str) -> str:
        calls.append(symbol)
        if len(calls) < 2:
            raise OSError("timeout")
        return TEXT

    slept: list[float] = []
    bars = SinaProvider(fetch=flaky, sleep=slept.append).daily_bars(
        "SPY", date(2026, 10, 1), date(2026, 10, 28)
    )
    assert len(bars) == 3 and slept == [2.0]


def test_gives_up_and_empty_range() -> None:
    def down(symbol: str) -> str:
        raise OSError("down")

    with pytest.raises(DataUnavailableError, match="OSError"):
        SinaProvider(fetch=down, sleep=lambda s: None).daily_bars(
            "SPY", date(2026, 10, 1), date(2026, 10, 28)
        )
    with pytest.raises(DataUnavailableError, match="没有"):
        SinaProvider(fetch=lambda s: TEXT, sleep=lambda s: None).daily_bars(
            "SPY", date(2026, 1, 1), date(2026, 1, 31)
        )


def test_paper_uses_tiger_and_sina(monkeypatch: pytest.MonkeyPatch) -> None:
    from quant.core.settings import load_settings

    monkeypatch.setenv("TRADING_MODE", "paper")
    s = load_settings()
    assert (s.data.primary, s.data.secondary) == ("tiger", "sina")
    monkeypatch.setenv("TRADING_MODE", "sim")
    assert load_settings().data.primary == "yfinance"
