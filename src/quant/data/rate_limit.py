"""同一数据卷上的进程共享 HTTP 请求节流；锁文件只保存时间，不保存密钥。"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def request_slot(path: Path, interval: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            try:
                last = float(f.read())
            except ValueError:
                last = 0.0
            wait = min(interval, max(0.0, last + interval - time.time()))
            if wait:
                time.sleep(wait)
            try:
                yield
            finally:
                f.seek(0)
                f.truncate()
                f.write(str(time.time()))
                f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
