"""测试共用的假对象。"""

from collections.abc import Mapping
from typing import Any


class FakeFetch:
    """按顺序返回给定的 (状态码, 内容)，最后一个重复使用；记录每次调用的网址与请求头。"""

    def __init__(self, *responses: tuple[int, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
        self.calls.append((url, dict(headers)))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r
