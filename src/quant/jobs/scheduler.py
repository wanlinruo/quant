"""scheduler：按美东时间与 XNYS 日历触发每日任务（PROJECT_PLAN 第 8 章）。

    python -m quant.jobs.scheduler

没有用 APScheduler 的 cron 触发器：交易日、半日市都要查日历，cron 表达不了。
改为每 30 秒检查一次当天（美东日期）的计划：
- 非交易日没有任务。
- daily_execute 在 schedule.daily_execute（默认 09:50）运行，完成后立即运行 daily_report；
  启动时已过当日执行时段（收盘前 stop_before_close_minutes）则不再执行，有待执行信号时提醒。
- daily_data / daily_signal 按配置时间（默认 16:30 / 17:00）；半日市按提前收盘的时长整体提前
  （13:00 收盘 → 13:30 / 14:00）。
- daily_signal 只在当日 daily_data 成功后运行。
- 进程内每个任务每天最多尝试一次；跨重启由 job_runs 保证幂等（jobs/runner.py）。
- 收到 SIGTERM / SIGINT 后，当前任务完成再退出。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dtime
from types import FrameType

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.core.calendar import TradingCalendar
from quant.core.settings import ConfigError, ScheduleSettings, load_settings
from quant.db.models import Signal
from quant.engine.executor import ET
from quant.jobs.daily import JOB_FUNCS, QUIET, JobContext
from quant.jobs.runner import job_status, run_job
from quant.notify.base import Level

log = logging.getLogger("quant.scheduler")
TICK_SECONDS = 30


def _at(d: date, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    return datetime.combine(d, dtime(int(hh), int(mm)), ET)


def day_plan(
    d: date, schedule: ScheduleSettings, cal: TradingCalendar
) -> list[tuple[str, datetime]]:
    """当天的任务与计划时间（美东），按时间排序；daily_report 紧随 daily_execute。"""
    if not cal.is_trading_day(d):
        return []
    early = _at(d, "16:00") - cal.session_close(d)  # 半日市提前的时长
    execute = max(_at(d, schedule.daily_execute), cal.session_open(d))
    plan = [
        ("daily_execute", execute),
        ("daily_report", execute),
        ("daily_data", _at(d, schedule.daily_data) - early),
        ("daily_signal", _at(d, schedule.daily_signal) - early),
    ]
    return sorted(plan, key=lambda x: x[1])


class Scheduler:
    def __init__(self, ctx: JobContext, now: Callable[[], datetime] | None = None) -> None:
        self.ctx = ctx
        self._now = now or ctx.now
        self._attempted: set[tuple[str, date]] = set()
        self.stop_requested = False

    def _ready(self, name: str, d: date) -> bool:
        with Session(self.ctx.engine) as s:
            if name == "daily_signal":
                return job_status(s, "daily_data", d) == "success"
            if name == "daily_report":
                return job_status(s, "daily_execute", d) in ("success", "failed")
        return True

    def _execute_deadline(self, d: date) -> datetime:
        stop = timedelta(minutes=self.ctx.settings.executor.stop_before_close_minutes)
        return self.ctx.calendar.session_close(d) - stop

    def _missed(self, d: date) -> None:
        """启动时已过当日执行时段：不再下单；有待执行的信号时提醒一次。"""
        prev = self.ctx.calendar.previous_trading_day(d)
        with Session(self.ctx.engine) as s:
            if job_status(s, "daily_execute", d) is not None:
                return
            pending = s.scalar(
                select(Signal.id).where(
                    Signal.strategy == self.ctx.strategy_name, Signal.signal_date == prev
                )
            )
        log.warning("job=daily_execute trade_date=%s 已过执行时段，今日不执行", d)
        if pending is not None:
            self.ctx.notifier.send(
                f"[{self.ctx.mode}] 错过执行 {d}",
                f"scheduler 在执行时段结束后才运行，{prev} 的信号今日未执行；"
                "下一交易日按新信号执行。",
                Level.WARNING,
            )

    def tick(self) -> list[tuple[str, str]]:
        """运行所有到点、未尝试过的任务；返回 (任务, 结果)。"""
        now = self._now()
        d = now.astimezone(ET).date()
        results = []
        for name, at in day_plan(d, self.ctx.settings.schedule, self.ctx.calendar):
            if self.stop_requested or now < at or (name, d) in self._attempted:
                continue
            if not self._ready(name, d):
                continue
            if name == "daily_execute" and now >= self._execute_deadline(d):
                self._attempted.add((name, d))
                self._missed(d)
                continue
            self._attempted.add((name, d))
            fn = JOB_FUNCS[name]
            result = run_job(
                self.ctx.engine,
                name,
                d,
                lambda fn=fn: fn(self.ctx, d),  # type: ignore[misc]
                self.ctx.notifier,
                quiet=QUIET,
            )
            results.append((name, result))
            now = self._now()
        return results

    def run_forever(self, sleep: Callable[[float], None] = time.sleep) -> None:
        log.info("scheduler 启动：mode=%s strategy=%s", self.ctx.mode, self.ctx.strategy_name)
        while not self.stop_requested:
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick 失败")  # 数据库暂时不可用等，下个周期重试
            sleep(TICK_SECONDS)
        log.info("scheduler 退出")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="scheduler").parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as e:
        log.error("配置错误：%s", e)
        return 1
    if settings.database_url is None:
        log.error("未配置 QUANT_DATABASE_URL")
        return 1
    from quant.db.backtests import make_engine
    from quant.notify.wecom import make_notifier

    engine = make_engine(settings.database_url.get_secret_value())
    ctx = JobContext.from_settings(settings, engine, make_notifier(settings))
    sched = Scheduler(ctx)

    def _stop(signum: int, frame: FrameType | None) -> None:
        log.info("收到信号 %s，当前任务完成后退出", signum)
        sched.stop_requested = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    sched.run_forever()
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
