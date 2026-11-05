"""scheduler：按美东时间与 XNYS 日历触发每日任务（PROJECT_PLAN 第 8 章）。

    python -m quant.jobs.scheduler

没有用 APScheduler 的 cron 触发器：交易日、半日市都要查日历，cron 表达不了。
改为每 30 秒检查一次当天（美东日期）的计划：
- 交易主链只在交易日运行；market_data 在目标交易日的次一自然日 00:10 ET 执行，
  因此周六/假期也可能仅运行上一交易日市场日线任务。
- daily_execute 在 schedule.daily_execute（默认 09:50）运行，完成后立即运行 daily_report；
  启动时已过当日执行时段（收盘前 stop_before_close_minutes）则不再执行，有待执行信号时提醒。
- daily_data / daily_signal 按配置时间（默认 16:30 / 17:00）；半日市按提前收盘的时长整体提前
  （13:00 收盘 → 13:30 / 14:00）。
- daily_signal 只在当日 daily_data 成功后运行。
- 每个数据日最多尝试一次，market_data 在跨日窗口暂时未就绪除外：间隔 15 分钟，最多重试 3 次；
  次数与间隔从 job_attempts 恢复，在任务锁内再次检查；其他失败仍需人工确认。
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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant.core.calendar import TradingCalendar, get_calendar
from quant.core.settings import ConfigError, ScheduleSettings, load_settings
from quant.db.models import JobAttempt, JobRun, Signal
from quant.engine.executor import ET
from quant.jobs.daily import JOB_FUNCS, QUIET, JobContext
from quant.jobs.runner import job_status, run_job
from quant.market.job import MarketDataPending
from quant.notify.base import Level

log = logging.getLogger("quant.scheduler")
TICK_SECONDS = 30
MARKET_RETRY_INTERVAL = timedelta(minutes=15)
MARKET_MAX_ATTEMPTS = 4  # 首次 + 3 次重试


def market_retry_due(
    s: Session,
    row: JobRun,
    now: datetime,
    *,
    schedule: ScheduleSettings | None = None,
    cal: TradingCalendar | None = None,
) -> bool:
    """仅重试次一自然日的市场数据；旧版本跨日前失败不消耗新窗口预算。"""
    if (
        row.job_name != "market_data"
        or row.status != "failed"
        or row.trade_date + timedelta(days=1) != now.astimezone(ET).date()
        or not (row.error or "").startswith(f"{MarketDataPending.__name__}:")
    ):
        return False
    cal = cal or get_calendar()
    if not cal.is_trading_day(row.trade_date):
        return False
    at = dict(day_plan(row.trade_date, schedule or ScheduleSettings(), cal))["market_data"]
    if now < at or now >= market_deadline(row.trade_date, cal):
        return False
    where = (
        JobAttempt.mode == row.mode,
        JobAttempt.job_name == row.job_name,
        JobAttempt.trade_date == row.trade_date,
        JobAttempt.started_at >= at,
    )
    count = s.scalar(select(func.count()).select_from(JobAttempt).where(*where)) or 0
    if count >= MARKET_MAX_ATTEMPTS:
        return False
    last = s.scalar(
        select(JobAttempt).where(*where).order_by(JobAttempt.attempt.desc()).limit(1)
    )  # fmt: skip
    return last is None or bool(
        last.result == "failed"
        and last.finished_at is not None
        and now >= last.finished_at + MARKET_RETRY_INTERVAL
    )


def _at(d: date, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    return datetime.combine(d, dtime(int(hh), int(mm)), ET)


def day_plan(
    d: date, schedule: ScheduleSettings, cal: TradingCalendar
) -> list[tuple[str, datetime]]:
    """按数据交易日归属的计划；market_data 在次一自然日，半日市也不提前它。"""
    if not cal.is_trading_day(d):
        return []
    early = _at(d, "16:00") - cal.session_close(d)  # 半日市提前的时长
    execute = max(_at(d, schedule.daily_execute), cal.session_open(d))
    plan = [
        ("daily_execute", execute),
        ("daily_report", execute),
        ("daily_data", _at(d, schedule.daily_data) - early),
        ("daily_signal", _at(d, schedule.daily_signal) - early),
        ("market_data", _at(d + timedelta(days=1), schedule.market_data)),
        ("market_extras", _at(d, schedule.market_extras) - early),
        ("stock_data", _at(d, schedule.stock_data) - early),
    ]
    return sorted(plan, key=lambda x: x[1])


def execution_plan(
    day: date,
    schedule: ScheduleSettings,
    cal: TradingCalendar,
) -> list[tuple[str, date, datetime]]:
    """自然日执行清单：(任务、数据交易日、计划时间)，周六也可补周五日线。"""
    out = [(name, day, at) for name, at in day_plan(day, schedule, cal) if name != "market_data"]
    previous = day - timedelta(days=1)
    out.extend(
        (name, previous, at)
        for name, at in day_plan(previous, schedule, cal)
        if name == "market_data"
    )
    return sorted(out, key=lambda item: item[2])


def market_deadline(day: date, cal: TradingCalendar) -> datetime:
    """临近下一个交易时段不再启动夜间补数，留给独立 quotes 的盘中兜底。"""
    return cal.session_open(cal.next_trading_day(day)) - timedelta(hours=2)


class Scheduler:
    def __init__(self, ctx: JobContext, now: Callable[[], datetime] | None = None) -> None:
        self.ctx = ctx
        self._now = now or ctx.now
        self._attempted: set[tuple[str, date]] = set()
        self.stop_requested = False

    def _ready(self, name: str, d: date) -> bool:
        with Session(self.ctx.engine) as s:
            if name == "daily_signal":
                return job_status(s, "daily_data", d, self.ctx.mode) == "success"
            if name == "daily_report":
                return job_status(s, "daily_execute", d, self.ctx.mode) in ("success", "failed")
        return True

    def _execute_deadline(self, d: date) -> datetime:
        stop = timedelta(minutes=self.ctx.settings.executor.stop_before_close_minutes)
        return self.ctx.calendar.session_close(d) - stop

    def _missed(self, d: date) -> None:
        """启动时已过当日执行时段：不再下单；有待执行的信号时提醒一次。"""
        prev = self.ctx.calendar.previous_trading_day(d)
        with Session(self.ctx.engine) as s:
            if job_status(s, "daily_execute", d, self.ctx.mode) is not None:
                return
            pending = s.scalar(
                select(Signal.id).where(
                    Signal.mode == self.ctx.mode,
                    Signal.strategy == self.ctx.strategy_name,
                    Signal.signal_date == prev,
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
        plan = execution_plan(d, self.ctx.settings.schedule, self.ctx.calendar)
        # 重启后先处理已到点的交易主链，过期的市场展示补数不能排在执行任务前。
        for name, trade_day, at in sorted(
            plan, key=lambda item: (item[0] == "market_data", item[2])
        ):
            if self.stop_requested or now < at:
                continue
            d = trade_day
            retry = False
            if name == "market_data":
                key = self.ctx.settings.massive_api_key
                if (
                    key is None
                    or not key.get_secret_value().strip()
                    or now >= market_deadline(d, self.ctx.calendar)
                ):
                    continue
                with Session(self.ctx.engine) as s:
                    row = s.scalar(select(JobRun).where(
                        JobRun.mode == self.ctx.mode, JobRun.job_name == name,
                        JobRun.trade_date == d,
                    ))  # fmt: skip
                    if row is not None and row.status == "failed":
                        retry = market_retry_due(
                            s, row, now, schedule=self.ctx.settings.schedule, cal=self.ctx.calendar
                        )
                        if not retry:
                            continue
            if (name, d) in self._attempted and not retry:
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
                lambda fn=fn, day=d: fn(self.ctx, day),  # type: ignore[misc]
                self.ctx.notifier,
                mode=self.ctx.mode,
                quiet=QUIET,
                retry_failed=(
                    lambda s, row: market_retry_due(
                        s,
                        row,
                        self._now(),
                        schedule=self.ctx.settings.schedule,
                        cal=self.ctx.calendar,
                    )
                )
                if retry
                else None,
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
