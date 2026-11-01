"""任务运行记录与幂等（PROJECT_PLAN 第 8 章）。

- 同一账户（mode）、同一任务、同一交易日只有一行 job_runs；已成功的不再执行。
- 每次实际执行另记一行 job_attempts（含重跑），用于系统页的“重试后成功”与运行详情。
- 失败的默认不自动重跑（避免反复告警），人工确认后用 quant job ... --rerun-failed 重跑。
- 停留在 running 的记录说明上次进程中途退出，允许重跑（执行任务会先恢复券商订单状态）。
- 用 PostgreSQL advisory lock 防止同一任务被两个进程同时执行。
"""

from __future__ import annotations

import logging
import traceback
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from quant.db.models import JobAttempt, JobRun
from quant.notify.base import Level, Notifier

log = logging.getLogger("quant.jobs")


class JobStopped(RuntimeError):
    """任务按规则停止（如熔断、风控拒绝），已经发送过通知；记为 failed，不再重复告警。"""


# 已自行发送通知的异常类型，run_job 不再重复通知
Quiet = tuple[type[BaseException], ...]


ERROR_SUMMARY_LEN = 500


def job_status(session: Session, name: str, day: date, mode: str) -> str | None:
    return session.scalar(
        select(JobRun.status).where(
            JobRun.mode == mode, JobRun.job_name == name, JobRun.trade_date == day
        )
    )


def _run_row(s: Session, mode: str, name: str, day: date) -> JobRun | None:
    return s.scalar(
        select(JobRun).where(JobRun.mode == mode, JobRun.job_name == name, JobRun.trade_date == day)
    )


@contextmanager
def job_lock(engine: Engine, name: str, day: date, mode: str) -> Iterator[bool]:
    key = zlib.crc32(f"quant:{mode}:{name}:{day.isoformat()}".encode())
    with engine.connect() as conn:
        got = bool(conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}))
        conn.commit()
        try:
            yield got
        finally:
            if got:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()


def run_job(
    engine: Engine,
    name: str,
    day: date,
    fn: Callable[[], str | None],
    notifier: Notifier,
    *,
    mode: str,
    rerun_failed: bool = False,
    quiet: Quiet = (JobStopped,),
) -> str:
    """返回 success / failed / skipped / locked。fn 返回的字符串作为日志摘要。"""
    tag = f"job={name} trade_date={day} mode={mode}"
    with job_lock(engine, name, day, mode) as got:
        if not got:
            log.warning("%s 正在其他进程中运行，跳过", tag)
            return "locked"
        with Session(engine) as s, s.begin():
            row = _run_row(s, mode, name, day)
            if row is not None and row.status == "success":
                log.info("%s 已成功，跳过", tag)
                return "skipped"
            if row is not None and row.status == "failed" and not rerun_failed:
                log.info("%s 已失败，需人工确认后重跑", tag)
                return "skipped"
            now = datetime.now(UTC)
            if row is None:
                s.add(JobRun(mode=mode, job_name=name, trade_date=day, status="running",
                             started_at=now))  # fmt: skip
            else:
                log.warning("%s 上次状态为 %s，重新执行", tag, row.status)
                row.status, row.started_at, row.finished_at, row.error = "running", now, None, None
            n = s.scalar(
                select(func.count()).select_from(JobAttempt).where(
                    JobAttempt.mode == mode, JobAttempt.job_name == name,
                    JobAttempt.trade_date == day,
                )
            ) or 0  # fmt: skip
            attempt = n + 1
            s.add(JobAttempt(mode=mode, job_name=name, trade_date=day, attempt=attempt,
                             started_at=now, result="running"))  # fmt: skip
        log.info("%s 开始", tag)
        try:
            detail = fn()
        except Exception as e:
            log.exception("%s 失败", tag)
            err = f"{type(e).__name__}: {e}"
            _finish(engine, mode, name, day, attempt, "failed",
                    f"{err}\n{traceback.format_exc(limit=8)}")  # fmt: skip
            if not isinstance(e, quiet):
                try:
                    notifier.send(f"任务失败 {name} {day}", err, Level.ERROR)
                except Exception:
                    log.exception("发送通知失败")
            return "failed"
        _finish(engine, mode, name, day, attempt, "success", None)
        log.info("%s 成功 %s", tag, detail or "")
        return "success"


def _finish(
    engine: Engine, mode: str, name: str, day: date, attempt: int, status: str, error: str | None
) -> None:
    now = datetime.now(UTC)
    with Session(engine) as s, s.begin():
        row = _run_row(s, mode, name, day)
        assert row is not None
        row.status, row.finished_at, row.error = status, now, error
        att = s.scalar(
            select(JobAttempt).where(
                JobAttempt.mode == mode, JobAttempt.job_name == name,
                JobAttempt.trade_date == day, JobAttempt.attempt == attempt,
            )
        )  # fmt: skip
        assert att is not None
        summary = error.splitlines()[0][:ERROR_SUMMARY_LEN] if error else None
        att.result, att.finished_at, att.error = status, now, summary
