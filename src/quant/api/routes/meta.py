"""健康检查与面板元信息（交易模式、账户、数据截至时间）。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.orm import Session

from quant.api import queries
from quant.api.deps import get_engine, get_settings
from quant.api.schemas import MODE_LABELS, MODES, AccountInfo, Meta, Mode

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _default_account(has: dict[Mode, bool], trading_mode: Mode) -> Mode:
    if has.get("live"):
        return "live"
    if has.get("paper"):
        return "paper"
    return trading_mode


@router.get("/meta")
def meta() -> Meta:
    settings = get_settings()
    eng = get_engine()
    has: dict[Mode, bool] = dict.fromkeys(MODES, False)
    data_as_of = None
    if eng is not None:
        with Session(eng) as s:
            has = queries.accounts(s)
            data_as_of = queries.last_job_finished(s)
    return Meta(
        trading_mode=settings.trading_mode,
        broker=settings.broker,
        database_configured=eng is not None,
        accounts=[AccountInfo(mode=m, label=MODE_LABELS[m], has_data=has[m]) for m in MODES],
        default_account=_default_account(has, settings.trading_mode),
        data_as_of=data_as_of,
    )
