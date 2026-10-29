"""告警接口（PROJECT_PLAN 第 8 章“通知时机”）。实现：LogNotifier（只写日志）、企业微信（#33）。

通知内容不得包含密钥、完整账户号。发送失败只记录日志，不影响交易流程。
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Protocol

log = logging.getLogger(__name__)


class Level(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Notifier(Protocol):
    def send(self, title: str, body: str, level: Level = Level.INFO) -> None: ...


class LogNotifier:
    """未配置 webhook 时使用。"""

    def send(self, title: str, body: str, level: Level = Level.INFO) -> None:
        log.log(logging.getLevelNamesMapping()[level.value], "[通知] %s\n%s", title, body)
