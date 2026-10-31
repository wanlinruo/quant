"""接口的依赖：配置、数据库、行情存储。"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from quant.core.settings import Settings, load_settings
from quant.data.store import ParquetStore
from quant.db.backtests import make_engine

NO_DB = "未配置数据库（QUANT_DATABASE_URL）"


@lru_cache
def get_settings() -> Settings:
    return load_settings()


@lru_cache
def get_engine() -> Engine | None:
    s = get_settings()
    return make_engine(s.database_url.get_secret_value()) if s.database_url else None


def get_session() -> Iterator[Session]:
    eng = get_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail=NO_DB)
    with Session(eng) as s:
        yield s


def get_store() -> ParquetStore:
    return ParquetStore(get_settings().data_dir)
