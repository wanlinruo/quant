"""系统状态（只读）：任务、失败、开关、回测队列。熔断开关只显示，不提供修改。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from quant.api import queries, system_view
from quant.api.deps import get_engine, get_session, get_settings
from quant.api.schemas import (
    FailureOut,
    JobStatusOut,
    Mode,
    QueueStatus,
    SysAttemptOut,
    SystemOverview,
)
from quant.core.calendar import get_calendar

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


@router.get("/system/overview")
def overview(mode: Mode) -> SystemOverview:
    """系统页概览（设计 5.5）：没有数据库时只返回日历与本地数据文件，并在横幅说明。"""
    eng = get_engine()
    settings, cal = get_settings(), get_calendar()
    if eng is None:
        return system_view.overview(None, mode, settings, cal)
    try:
        with Session(eng) as s:
            return system_view.overview(s, mode, settings, cal)
    except OperationalError as e:  # 数据库连不上：降级为只显示日历与本地数据文件
        reason = type(getattr(e, "orig", e)).__name__
        return system_view.overview(
            None, mode, settings, cal, no_db=f"{system_view.DB_DOWN}（{reason}）"
        )


@router.get("/system/job-runs")
def job_runs(
    s: SessionDep, mode: Mode, job: str, day: Annotated[date, Query(alias="date")]
) -> list[SysAttemptOut]:
    """某次任务的每次尝试：起止、耗时、结果、错误前 20 行。"""
    return system_view.job_attempts(s, mode, job, day)
