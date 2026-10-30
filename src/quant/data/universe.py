"""标普 500 历史成分名单（GitHub fja05680/sp500 的
`S&P 500 Historical Components & Changes (Updated).csv`：列 date,tickers，tickers 为逗号分隔）。

回测用完整历史；服务器每日任务用 sync_constituents 保持本地快照（最多每周下载一次）。
"""

from __future__ import annotations

import csv
import io
import logging
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from quant.data.providers.base import DataUnavailableError
from quant.strategies.universe import Constituents

log = logging.getLogger("quant.data")

SP500_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
STALE_DAYS = 120  # 成分变动通常每季度都有；超过此天数没有新记录，数据集可能停更


def parse_sp500_history(text: str) -> Constituents:
    snaps: dict[date, frozenset[str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if "date" not in row or "tickers" not in row or row["tickers"] is None:
            raise ValueError("成分名单格式不对：需要 date,tickers 两列")
        tickers = frozenset(t.strip() for t in row["tickers"].split(",") if t.strip())
        snaps[date.fromisoformat(row["date"])] = tickers
    return Constituents(snaps)


def load_constituents(path: Path) -> Constituents:
    return parse_sp500_history(path.read_text(encoding="utf-8"))


def constituents_path(data_dir: Path) -> Path:
    return data_dir / "universe" / "sp500_history.csv"


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:  # 固定的 https 地址
        return bytes(resp.read())


def sync_constituents(
    path: Path,
    today: date,
    fetch: Callable[[str], bytes] | None = None,
    max_age_days: int = 7,
) -> Constituents:
    """文件不存在或早于 max_age_days 天时重新下载；解析成功后才替换本地文件。

    下载失败但旧文件存在时沿用旧文件（记 warning）；都没有时抛 DataUnavailableError。
    下载内容无法解析时抛 ValueError，本地文件不变（数据集格式变化需要人工处理）。
    """
    fresh = path.exists() and (
        datetime.fromtimestamp(path.stat().st_mtime).date() > today - timedelta(days=max_age_days)
    )
    if fresh:
        return load_constituents(path)
    try:
        raw = (fetch or _http_get)(SP500_URL)
    except Exception as e:
        if path.exists():
            log.warning("成分名单下载失败，沿用本地文件：%s", e)
            return load_constituents(path)
        raise DataUnavailableError(f"成分名单下载失败且本地没有文件：{e}") from e
    text = raw.decode("utf-8")
    u = parse_sp500_history(text)  # 失败则抛出，不写文件
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return u


def stale_warning(u: Constituents, today: date, days: int = STALE_DAYS) -> str | None:
    last = max(u.snapshots)
    if (today - last).days > days:
        return f"标普 500 成分名单最后一条变动为 {last}，已超过 {days} 天，数据集可能停更"
    return None
