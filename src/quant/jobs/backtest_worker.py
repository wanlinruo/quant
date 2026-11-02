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
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from quant.core.calendar import get_calendar
from quant.core.settings import ConfigError, CostSettings, Settings, load_settings
from quant.data.backtest_data import make_runner
from quant.db.backtests import claim_next, fail_run, make_engine
from quant.db.models import BacktestRun
from quant.engine import robustness
from quant.engine.backtester import BacktestResult
from quant.engine.robustness import ROBUST_KEY
from quant.engine.runner import CASH_KEY, COSTS_KEY, BacktestSpec, run_spec, save_result

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
        is_child = run.parent_id is not None
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
    try:
        with Session(engine) as session, session.begin():
            save_result(session, run_id, result)
            if stored.get(ROBUST_KEY) and not is_child:
                _spawn_in_savepoint(session, run_id, spec)
    except Exception as e:  # 写库出错：记为失败并写入真实原因，worker 不退出
        log.exception("回测 #%s 保存结果失败", run_id)
        detail = f"保存结果失败：{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"
        with Session(engine) as session, session.begin():
            fail_run(session, run_id, detail)
        return run_id
    log.info("回测 #%s 完成：年化 %.2f%%", run_id, result.metrics.cagr * 100)
    return run_id


def _spawn_in_savepoint(session: Session, run_id: int, spec: BacktestSpec) -> None:
    """与保存结果在同一事务：worker 在两者之间崩溃也不会只剩父回测；生成失败只回滚子任务。"""
    try:
        with session.begin_nested():
            n = spawn_robustness(session, run_id, spec)
        log.info("回测 #%s：已生成 %d 个稳健性检验子任务", run_id, n)
    except Exception:  # 生成失败不影响父回测结果
        log.exception("回测 #%s 生成稳健性检验子任务失败", run_id)


def spawn_robustness(session: Session, parent_id: int, spec: BacktestSpec) -> int:
    """父回测完成后插入子回测（排在队尾）；返回子任务数。"""
    base = spec.stored_params()
    costs, cash = base[COSTS_KEY], base[CASH_KEY]
    variants = robustness.plan(spec.strategy, spec.params, spec.start, spec.end)
    n = 0
    for v in variants:
        c = robustness.scale_costs(costs, v.cost_mult) if v.cost_mult is not None else costs
        try:
            CostSettings(**c)
        except ValidationError:  # 放大后超出成本上限（如滑点 ≥ 10%）：跳过这一档
            log.info("回测 #%s：%s 超出成本上限，跳过", parent_id, v.label)
            continue
        n += 1
        session.add(
            BacktestRun(
                strategy=spec.strategy,
                params={**v.params, COSTS_KEY: c, CASH_KEY: cash},
                start=v.start,
                end=spec.end,
                status="queued",
                parent_id=parent_id,
                variant={
                    "kind": v.kind,
                    "label": v.label,
                    "x": v.x,
                    "y": v.y,
                    "neighbor": v.neighbor,
                    "cost_mult": v.cost_mult,
                },
            )
        )
    return n


STALE_RUNNING_ERROR = (
    "worker 重启时发现该任务仍在运行（进程可能因内存超限等被强制结束），已标记失败"
)


def fail_stale_running(engine: Engine) -> list[int]:
    """启动时把遗留的 running 任务标为失败（只有一个 worker，启动时不可能有正在跑的任务）。"""
    with Session(engine) as session, session.begin():
        ids = list(session.scalars(select(BacktestRun.id).where(BacktestRun.status == "running")))
        for run_id in ids:
            fail_run(session, run_id, STALE_RUNNING_ERROR)
    for run_id in ids:
        log.warning("回测 #%s 遗留为 running，已标记失败", run_id)
    return ids


class Backfill(Protocol):
    def maybe_step(self) -> None: ...


def worker_tick(process: Callable[[], int | None], backfill: Backfill | None) -> int | None:
    """处理一个回测任务；队列为空时让补数器补一步（服务器回测数据，#66）。"""
    run_id = process()
    if run_id is None and backfill is not None:
        backfill.maybe_step()
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
    fail_stale_running(engine)
    backfill = make_runner(settings, get_calendar())
    log.info("backtest-worker 启动%s", "（空闲时补齐 Tiingo 回测数据）" if backfill else "")
    while not stop.requested:
        if worker_tick(lambda: process_one(engine, settings), backfill) is None:
            if args.once:
                break
            time.sleep(args.poll_seconds)
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
