"""数据提供者共用的 JSON HTTP 请求：返回 (状态码, 解析后的内容)，HTTP 错误不抛异常。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

Fetch = Callable[[str, Mapping[str, str]], tuple[int, Any]]


def http_fetch(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # 固定的 https 地址
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


def http_get_text(url: str) -> str:
    """下载公开文本文件（如成分股 CSV）。"""
    with urllib.request.urlopen(url, timeout=60) as resp:  # 固定的 https 地址
        return str(resp.read().decode("utf-8"))
