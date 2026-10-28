"""Parquet 行情存储与 DuckDB 查询（PROJECT_PLAN 7.2）。

布局：{root}/bars/daily/symbol={SYMBOL}/data.parquet
字段：date, open, high, low, close, adj_close, volume, source, ingested_at

写入流程：读取已有文件 → 合并去重 → 写临时文件 → 原子替换。
值没有变化的行保留原来的 source 与 ingested_at，因此同样的数据重复写入，文件内容不变（幂等）。
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from quant.data.providers.base import BAR_COLUMNS, PRICE_COLUMNS, empty_bars, normalize_bars

_SYMBOL_DIR_RE = re.compile(r"symbol=([A-Z][A-Z0-9.]{0,9})")
STORED_COLUMNS = ("date", *BAR_COLUMNS, "source", "ingested_at")


class ParquetStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._daily = self.root / "bars" / "daily"

    def _path(self, symbol: str) -> Path:
        if not _SYMBOL_DIR_RE.fullmatch(f"symbol={symbol}"):
            raise ValueError(f"非法代码：{symbol!r}")
        return self._daily / f"symbol={symbol}" / "data.parquet"

    def symbols(self) -> list[str]:
        if not self._daily.is_dir():
            return []
        found = (_SYMBOL_DIR_RE.fullmatch(p.name) for p in self._daily.iterdir())
        return sorted(
            m.group(1) for m in found if m and (self._daily / m.group(0) / "data.parquet").is_file()
        )

    def _read_raw(self, symbol: str) -> pd.DataFrame | None:
        path = self._path(symbol)
        if not path.is_file():
            return None
        return pd.read_parquet(path).set_index("date")

    def write_bars(
        self, symbol: str, bars: pd.DataFrame, source: str, ingested_at: datetime | None = None
    ) -> int:
        """合并写入，返回新增或变化的行数。bars 为统一格式（见 providers/base.py）。"""
        new = normalize_bars(bars)
        if new.empty:
            return 0
        ts = (ingested_at or datetime.now(UTC)).astimezone(UTC)
        new["source"] = source
        new["ingested_at"] = pd.Timestamp(ts)
        old = self._read_raw(symbol)
        if old is not None:
            old = old.loc[:, list(new.columns)]
            new["ingested_at"] = new["ingested_at"].astype(old["ingested_at"].dtype)
            common = new.index.intersection(old.index)
            value_cols = [*BAR_COLUMNS, "source"]
            same = (
                (new.loc[common, value_cols] == old.loc[common, value_cols])
                | (new.loc[common, value_cols].isna() & old.loc[common, value_cols].isna())
            ).all(axis=1)
            unchanged = same[same].index
            new = new.drop(index=unchanged)
            if new.empty:
                return 0
            merged = pd.concat([old.drop(index=new.index, errors="ignore"), new]).sort_index()
        else:
            merged = new
        self._atomic_write(self._path(symbol), merged)
        return len(new)

    @staticmethod
    def _atomic_write(path: Path, df: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        out = df.reset_index().loc[:, list(STORED_COLUMNS)]
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".data.", suffix=".parquet.tmp")
        os.close(fd)
        try:
            out.to_parquet(tmp, index=False)
            os.replace(tmp, path)  # 同目录内原子替换，读者不会看到写了一半的文件
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def read_bars(
        self, symbol: str, start: date | None = None, end: date | None = None
    ) -> pd.DataFrame:
        """统一格式的日线；没有数据时返回空表。"""
        raw = self._read_raw(symbol)
        if raw is None:
            return empty_bars()
        return normalize_bars(raw.loc[:, list(BAR_COLUMNS)], start, end)

    def ingestion_info(self, symbol: str) -> pd.DataFrame:
        """每行的 source 与 ingested_at，用于排查数据来源。"""
        raw = self._read_raw(symbol)
        return (
            pd.DataFrame(columns=["source", "ingested_at"])
            if raw is None
            else raw[["source", "ingested_at"]]
        )

    def prices(
        self,
        symbols: Sequence[str],
        start: date | None = None,
        end: date | None = None,
        field: str = "adj_close",
    ) -> pd.DataFrame:
        """宽表：索引为日期，列为代码，值为指定字段（默认含分红复权价）。

        签名与 Context 的 loader 一致，可直接注入策略。
        """
        if field not in PRICE_COLUMNS:
            raise ValueError(f"非法字段：{field}")
        cols = {s: self.read_bars(s, start, end)[field] for s in symbols}
        df = pd.DataFrame(cols, columns=list(symbols))
        df.index.name = "date"
        return df.sort_index()

    def query(self, sql: str) -> pd.DataFrame:
        """用 DuckDB 查询全部日线；表名为 bars（含 symbol 列）。只读。"""
        con = duckdb.connect()
        try:
            if self.symbols():
                pattern = str(self._daily / "symbol=*" / "data.parquet").replace("'", "''")
                con.execute(
                    "CREATE VIEW bars AS SELECT * FROM "
                    f"read_parquet('{pattern}', hive_partitioning = true)"
                )
            else:
                cols = ", ".join(f"NULL::DOUBLE AS {c}" for c in PRICE_COLUMNS)
                con.execute(
                    f"CREATE VIEW bars AS SELECT NULL::DATE AS date, {cols},"
                    " NULL::BIGINT AS volume, NULL::VARCHAR AS source,"
                    " NULL::TIMESTAMPTZ AS ingested_at, NULL::VARCHAR AS symbol WHERE false"
                )
            return con.execute(sql).df()
        finally:
            con.close()
