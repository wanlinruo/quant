"""系统状态（只读）：任务、失败、开关、回测队列。熔断开关只显示，不提供修改。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from quant.api import queries
from quant.api.deps import get_session
from quant.api.schemas import FailureOut, JobStatusOut, Mode, QueueStatus

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/system/jobs")
def jobs(s: SessionDep, mode: Mode | None = None) -> list[JobStatusOut]:
    return queries.job_status(s, mode)


@router.get("/system/failures")
def failures(s: SessionDep) -> list[FailureOut]:
    return queries.recent_failures(s)


@router.get("/system/flags")
def flags(s: SessionDep) -> dict[str, str]:
    return queries.flags(s)


@router.get("/system/queue")
def queue(s: SessionDep) -> QueueStatus:
    return queries.queue_status(s)
