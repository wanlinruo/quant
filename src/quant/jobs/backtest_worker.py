"""backtest-worker：轮询 backtest_runs 队列执行回测（PROJECT_PLAN 第 9 章“任务化执行”）。

    python -m quant.jobs.backtest_worker [--once] [--poll-seconds 5]

- 领取用 FOR UPDATE SKIP LOCKED，多个 worker 并发不会重复执行同一任务。
- 每个任务：queued → running →（done | failed，失败原因写入 error）。
- 收到 SIGTERM / SIGINT 时处理完当前任务再退出。
- 进程被强杀时任务会停留在 running，需要人工改回 queued（Phase 4 的系统页会显示）。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
import traceback
from collections.abc import Callable
from types import FrameType

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from quant.core.settings import ConfigError, Settings, load_settings
from quant.db.backtests import claim_next, fail_run, make_engine
from quant.engine.backtester import BacktestResult
from quant.engine.runner import BacktestSpec, run_spec, save_result

log = logging.getLogger("quant.backtest_worker")

Runner = Callable[[BacktestSpec, Settings], BacktestResult]


def process_one(engine: Engine, settings: Settings, runner: Runner = run_spec) -> int | None:
    """领取并执行一个任务；没有任务时返回 None，否则返回任务 id。"""
    with Session(engine) as session, session.begin():
        run = claim_next(session)
        if run is None:
            return None
        run_id, strategy, start, end = run.id, run.strategy, run.start, run.end
        stored = dict(run.params)
    log.info("开始回测 #%s %s %s~%s", run_id, strategy, start, end)
    try:
        spec = BacktestSpec.from_stored(strategy, start, end, stored, settings)
        result = runner(spec, settings)
    except Exception as e:
        log.exception("回测 #%s 失败", run_id)
        detail = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"
        with Session(engine) as session, session.begin():
            fail_run(session, run_id, detail)
        return run_id
    with Session(engine) as session, session.begin():
        save_result(session, run_id, result)
    log.info("回测 #%s 完成：年化 %.2f%%", run_id, result.metrics.cagr * 100)
    return run_id


class _Stop:
    requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        log.info("收到信号 %s，处理完当前任务后退出", signum)
        self.requested = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest-worker")
    ap.add_argument("--once", action="store_true", help="只处理队列中现有任务，然后退出")
    ap.add_argument("--poll-seconds", type=float, default=5.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as e:
        log.error("配置错误：%s", e)
        return 1
    if settings.database_url is None:
        log.error("未配置 QUANT_DATABASE_URL")
        return 1
    engine = make_engine(settings.database_url.get_secret_value())
    stop = _Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("backtest-worker 启动")
    while not stop.requested:
        if process_one(engine, settings) is None:
            if args.once:
                break
            time.sleep(args.poll_seconds)
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
