"""接口的依赖：配置、数据库、行情存储。"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from quant.core.settings import Settings, load_settings
from quant.data.events import load_events
from quant.data.store import ParquetStore
from quant.data.universe import constituents_path, load_constituents
from quant.data.universe_bars import universe_store
from quant.db.backtests import make_engine
from quant.strategies.events import EventCalendar
from quant.strategies.universe import Constituents

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


def get_universe_store() -> ParquetStore:
    """候选池日线（新浪，未复权）：主数据目录没有的股票从这里取。"""
    return universe_store(get_settings().data_dir)


def get_events() -> EventCalendar:
    """财报日（实盘同步的 + 回测用的，存在时）与 FOMC。"""
    return load_events(get_settings().data_dir)


def get_constituents() -> Constituents | None:
    """标普 500 历史成分（判断“属于哪些指数”）；文件不存在时为 None。"""
    path = constituents_path(get_settings().data_dir)
    return load_constituents(path) if path.exists() else None
