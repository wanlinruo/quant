"""企业微信群机器人告警（#36 确认渠道，#33）。

接口（官方文档 https://developer.work.weixin.qq.com/document/path/91770 ）：
- POST https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KEY，JSON 正文
- markdown 消息 content 最长 4096 字节（UTF-8），超出时截断
- 每个机器人每分钟最多 20 条，超出时等待

webhook 地址含 key，属于密钥：只来自环境变量 QUANT_NOTIFY_WEBHOOK_URL，
日志与异常信息中只出现脱敏后的地址。发送失败只记录日志，不抛出，不影响交易流程。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

from quant.core.settings import ConfigError, Settings
from quant.notify.base import Level, LogNotifier, Notifier

log = logging.getLogger(__name__)

HOST = "qyapi.weixin.qq.com"
PATH = "/cgi-bin/webhook/send"
MAX_BYTES = 4096
RATE_LIMIT = 20  # 条/分钟
_COLOR = {Level.INFO: "info", Level.WARNING: "comment", Level.ERROR: "warning"}
_MARK = {Level.INFO: "✅", Level.WARNING: "⚠️", Level.ERROR: "🚨"}

# (url, body bytes, timeout 秒) → 响应 JSON
Post = Callable[[str, bytes, float], dict[str, object]]


def _http_post(url: str, body: bytes, timeout: float) -> dict[str, object]:
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # 地址已由 validate_url 校验
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    key = parse_qs(parts.query).get("key", [""])[0]
    if parts.scheme != "https" or parts.hostname != HOST or parts.path != PATH or not key:
        raise ConfigError(
            "QUANT_NOTIFY_WEBHOOK_URL 应为 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
        )
    return url


def truncate_utf8(text: str, limit: int = MAX_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix = "\n…（已截断）"
    keep = limit - len(suffix.encode("utf-8"))
    return raw[:keep].decode("utf-8", errors="ignore") + suffix


def render(title: str, body: str, level: Level) -> str:
    """markdown：标题按级别着色，正文原样放入引用块。"""
    head = f'{_MARK[level]} <font color="{_COLOR[level]}">**{title}**</font>'
    quoted = "\n".join(f"> {line}" if line else ">" for line in body.splitlines())
    return truncate_utf8(f"{head}\n{quoted}" if quoted else head)


class WeComNotifier:
    def __init__(
        self,
        url: str,
        *,
        post: Post = _http_post,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 2,
        timeout: float = 10.0,
    ) -> None:
        self._url = validate_url(url)
        self._post = post
        self._now = now
        self._sleep = sleep
        self._retries = retries
        self._timeout = timeout
        self._sent: deque[float] = deque()

    @property
    def masked_url(self) -> str:
        return f"https://{HOST}{PATH}?key=***"

    def _throttle(self) -> None:
        while self._sent and self._now() - self._sent[0] >= 60:
            self._sent.popleft()
        if len(self._sent) >= RATE_LIMIT:
            self._sleep(60 - (self._now() - self._sent[0]))
            self._sent.popleft()
        self._sent.append(self._now())

    def send(self, title: str, body: str, level: Level = Level.INFO) -> None:
        payload = {"msgtype": "markdown", "markdown": {"content": render(title, body, level)}}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for attempt in range(self._retries + 1):
            self._throttle()
            try:
                resp = self._post(self._url, data, self._timeout)
            except Exception as e:  # 网络错误：重试
                err = f"{type(e).__name__}: {e}".replace(self._url, self.masked_url)
                log.warning("企业微信发送失败（第 %d 次）：%s", attempt + 1, err)
                self._sleep(2**attempt)
                continue
            if resp.get("errcode") == 0:
                return
            # 接口返回错误（key 无效、内容非法、限流等）：记录后不再重试
            log.error(
                "企业微信返回错误：errcode=%s errmsg=%s", resp.get("errcode"), resp.get("errmsg")
            )
            return
        log.error("企业微信发送失败，已放弃：%s", title)


def make_notifier(settings: Settings) -> Notifier:
    """配置了 QUANT_NOTIFY_WEBHOOK_URL 时用企业微信，否则只写日志。"""
    if settings.notify_webhook_url is None:
        log.warning("未配置 QUANT_NOTIFY_WEBHOOK_URL，通知只写入日志")
        return LogNotifier()
    return WeComNotifier(settings.notify_webhook_url.get_secret_value())
