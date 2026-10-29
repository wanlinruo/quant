"""企业微信通知：用假的 HTTP 发送函数测试，不访问网络。"""

import json
import logging
from typing import Any

import pytest

from quant.core.settings import ConfigError, Settings
from quant.notify.base import Level, LogNotifier
from quant.notify.wecom import (
    MAX_BYTES,
    WeComNotifier,
    make_notifier,
    render,
    truncate_utf8,
    validate_url,
)

KEY = "693a91f6-7xxx-4bc4-97a0-0ec2sifa5aaa"
URL = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={KEY}"


class FakeHttp:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> dict[str, object]:
        self.calls.append((url, json.loads(body)))
        r = self.responses.pop(0) if self.responses else {"errcode": 0, "errmsg": "ok"}
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def notifier(http: FakeHttp, clock: Clock | None = None) -> WeComNotifier:
    c = clock or Clock()
    return WeComNotifier(URL, post=http, now=c.now, sleep=c.sleep)


@pytest.mark.parametrize(
    "bad",
    [
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "https://evil.example.com/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
        "https://qyapi.weixin.qq.com/other?key=x",
    ],
)
def test_validate_url(bad: str) -> None:
    assert validate_url(URL) == URL
    with pytest.raises(ConfigError) as e:
        validate_url(bad)
    assert "key=x" not in str(e.value)


def test_send_markdown() -> None:
    http = FakeHttp([])
    notifier(http).send("执行完成 2026-10-29", "卖 SPY 160\n买 QQQ 191", Level.INFO)
    url, payload = http.calls[0]
    assert url == URL
    assert payload["msgtype"] == "markdown"
    content = payload["markdown"]["content"]
    assert '<font color="info">**执行完成 2026-10-29**</font>' in content
    assert "> 卖 SPY 160\n> 买 QQQ 191" in content


def test_error_level_color() -> None:
    assert '<font color="warning">' in render("熔断", "对账不一致", Level.ERROR)


def test_truncate_utf8() -> None:
    text = "中" * 3000  # 9000 字节
    out = truncate_utf8(text)
    assert len(out.encode("utf-8")) <= MAX_BYTES and out.endswith("（已截断）")
    assert truncate_utf8("短") == "短"
    assert len(render("t", "中" * 3000, Level.INFO).encode("utf-8")) <= MAX_BYTES


def test_retry_network_error_and_redact(caplog: pytest.LogCaptureFixture) -> None:
    http = FakeHttp([OSError(f"连接 {URL} 失败"), {"errcode": 0}])
    with caplog.at_level(logging.WARNING):
        notifier(http).send("t", "b")
    assert len(http.calls) == 2
    assert KEY not in caplog.text and "key=***" in caplog.text


def test_gives_up_without_raising(caplog: pytest.LogCaptureFixture) -> None:
    http = FakeHttp([OSError("x")] * 3)
    notifier(http).send("t", "b")
    assert len(http.calls) == 3
    assert "已放弃" in caplog.text


def test_api_error_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    http = FakeHttp([{"errcode": 93000, "errmsg": "invalid webhook url"}])
    notifier(http).send("t", "b")
    assert len(http.calls) == 1
    assert "93000" in caplog.text and KEY not in caplog.text


def test_rate_limit_20_per_minute() -> None:
    http, clock = FakeHttp([]), Clock()
    n = notifier(http, clock)
    for _ in range(21):
        n.send("t", "b")
    assert len(http.calls) == 21
    assert clock.slept == [60]  # 第 21 条等到第 1 条满 60 秒


def test_make_notifier() -> None:
    assert isinstance(make_notifier(Settings(broker="sim")), LogNotifier)
    s = Settings(broker="sim", notify_webhook_url=URL)
    assert isinstance(make_notifier(s), WeComNotifier)
    assert KEY not in repr(s)


def test_cli_notify_test(capsys: pytest.CaptureFixture[str]) -> None:
    from quant.cli import notify_test

    notify_test(Settings(broker="sim"))
    assert "LogNotifier" in capsys.readouterr().out
