"""任务运行记录与幂等（PROJECT_PLAN 第 8 章）。

- 同一任务、同一交易日只有一行 job_runs；已成功的不再执行。
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

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from quant.db.models import JobRun
from quant.notify.base import Level, Notifier

log = logging.getLogger("quant.jobs")


class JobStopped(RuntimeError):
    """任务按规则停止（如熔断、风控拒绝），已经发送过通知；记为 failed，不再重复告警。"""


# 已自行发送通知的异常类型，run_job 不再重复通知
Quiet = tuple[type[BaseException], ...]


def job_status(session: Session, name: str, day: date) -> str | None:
    return session.scalar(
        select(JobRun.status).where(JobRun.job_name == name, JobRun.trade_date == day)
    )


@contextmanager
def job_lock(engine: Engine, name: str, day: date) -> Iterator[bool]:
    key = zlib.crc32(f"quant:{name}:{day.isoformat()}".encode())
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
    rerun_failed: bool = False,
    quiet: Quiet = (JobStopped,),
) -> str:
    """返回 success / failed / skipped / locked。fn 返回的字符串作为日志摘要。"""
    tag = f"job={name} trade_date={day}"
    with job_lock(engine, name, day) as got:
        if not got:
            log.warning("%s 正在其他进程中运行，跳过", tag)
            return "locked"
        with Session(engine) as s, s.begin():
            row = s.scalar(select(JobRun).where(JobRun.job_name == name, JobRun.trade_date == day))
            if row is not None and row.status == "success":
                log.info("%s 已成功，跳过", tag)
                return "skipped"
            if row is not None and row.status == "failed" and not rerun_failed:
                log.info("%s 已失败，需人工确认后重跑", tag)
                return "skipped"
            now = datetime.now(UTC)
            if row is None:
                s.add(JobRun(job_name=name, trade_date=day, status="running", started_at=now))
            else:
                log.warning("%s 上次状态为 %s，重新执行", tag, row.status)
                row.status, row.started_at, row.finished_at, row.error = "running", now, None, None
        log.info("%s 开始", tag)
        try:
            detail = fn()
        except Exception as e:
            log.exception("%s 失败", tag)
            err = f"{type(e).__name__}: {e}"
            _finish(engine, name, day, "failed", f"{err}\n{traceback.format_exc(limit=8)}")
            if not isinstance(e, quiet):
                try:
                    notifier.send(f"任务失败 {name} {day}", err, Level.ERROR)
                except Exception:
                    log.exception("发送通知失败")
            return "failed"
        _finish(engine, name, day, "success", None)
        log.info("%s 成功 %s", tag, detail or "")
        return "success"


def _finish(engine: Engine, name: str, day: date, status: str, error: str | None) -> None:
    with Session(engine) as s, s.begin():
        row = s.scalar(select(JobRun).where(JobRun.job_name == name, JobRun.trade_date == day))
        assert row is not None
        row.status, row.finished_at, row.error = status, datetime.now(UTC), error
