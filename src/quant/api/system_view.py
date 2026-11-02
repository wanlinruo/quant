"""系统页概览（#71 A 批，设计 5.5）：总状态、状态卡、任务网格与今天时间线、数据源与额度、
风控限额、接下来的交易日、告警与事件、回测队列。只读：数据库记录、调度计划、日历与本地数据文件。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api.schemas import (
    SysAttemptOut,
    SysBannerOut,
    SysCardOut,
    SysCellOut,
    SysDayOut,
    SysEventOut,
    SysGridOut,
    SysItemOut,
    SysJobRowOut,
    SysQueueOut,
    SysRiskOut,
    SysSourceOut,
    SystemOverview,
    SysTodayOut,
)
from quant.api.system_resources import resources
from quant.core.calendar import TradingCalendar
from quant.core.settings import Settings
from quant.data.events import load_fomc_dates
from quant.data.market_store import (
    calendar_path,
    macro_path,
    market_dir,
    news_path,
    snapshot_path,
)
from quant.db.models import (
    AccountSnapshotRow,
    BacktestRun,
    Fill,
    JobAttempt,
    JobRun,
    Order,
    PositionSnapshot,
    SystemFlag,
)
from quant.jobs.daily import JOBS
from quant.jobs.scheduler import day_plan

ET = ZoneInfo("America/New_York")
GRID_DAYS = 30
STALE_DAYS = 2
WEEK = "一二三四五六日"
NO_DB = "未配置数据库（QUANT_DATABASE_URL），只能显示日历与本地数据文件"


# 与 scheduler 一致：daily_report 在 daily_execute 结束（成功或失败）后运行；
# daily_signal 只在 daily_data 成功后运行。
UPSTREAM = {
    "daily_report": ("daily_execute", ("success", "failed")),
    "daily_signal": ("daily_data", ("success",)),
}
GRACE = timedelta(minutes=10)  # 调度每 30 秒检查一次，计划时间后留出余量


def _now() -> datetime:
    return datetime.now(UTC)


def _today(cal: TradingCalendar, now: datetime) -> date:
    d = now.astimezone(ET).date()
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def _first_line(s: str | None) -> str | None:
    return s.strip().splitlines()[0] if s and s.strip() else None


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return (b - a).total_seconds() if a and b else None


def _grid(
    s: Session, mode: str, days: list[date], cal: TradingCalendar, settings: Settings, now: datetime
) -> SysGridOut:
    runs = s.scalars(select(JobRun).where(JobRun.mode == mode)).all()
    by = {(r.job_name, r.trade_date): r for r in runs}
    first: dict[str, date] = {}
    for r in runs:
        first[r.job_name] = min(first.get(r.job_name, r.trade_date), r.trade_date)
    counts: dict[tuple[str, date], int] = defaultdict(int)
    for a in s.scalars(
        select(JobAttempt).where(JobAttempt.mode == mode, JobAttempt.trade_date >= days[0])
    ):
        counts[(a.job_name, a.trade_date)] += 1
    plans = {d: dict(day_plan(d, settings.schedule, cal)) for d in days}
    rows = []
    for job in JOBS:
        cells, durs, ok, have = [], [], 0, 0
        for d in days:
            run = by.get((job, d))
            n = counts.get((job, d), 0)
            if run is None:
                planned = plans[d].get(job)
                up = UPSTREAM.get(job)
                up_run = by.get((up[0], d)) if up else None
                if job not in first or d < first[job]:
                    st = "na"  # 该任务上线之前
                elif planned is not None and planned + GRACE > now:
                    st = "pending"
                elif up and (up_run is None or up_run.status not in (*up[1], "running")):
                    st = "blocked"  # 上游没跑或失败，本任务不会运行（上游那一格已告警）
                elif up_run is not None and up_run.status == "running":
                    st = "pending"
                else:
                    done = up_run.finished_at if up_run else None
                    st = "pending" if done is not None and done + GRACE > now else "missing"
                cells.append(SysCellOut(date=d, status=st, attempts=n))
                continue
            have += 1
            dur = _secs(run.started_at, run.finished_at)
            if run.status == "success":
                ok += 1
                st = "retry_success" if n > 1 else "success"
                if dur is not None:
                    durs.append(dur)
            else:
                st = run.status
            cells.append(SysCellOut(date=d, status=st, attempts=n, duration_s=dur))
        rows.append(
            SysJobRowOut(
                job=job,
                cells=cells,
                success_rate=ok / have if have else None,
                avg_s=sum(durs) / len(durs) if durs else None,
            )
        )
    return SysGridOut(dates=days, jobs=rows)


def _timeline(
    grid: SysGridOut, s: Session, mode: str, today: date, cal: TradingCalendar, settings: Settings
) -> list[SysTodayOut]:
    runs = {
        r.job_name: r
        for r in s.scalars(select(JobRun).where(JobRun.mode == mode, JobRun.trade_date == today))
    }
    cell = {row.job: row.cells[-1] for row in grid.jobs}
    out = []
    for job, at in day_plan(today, settings.schedule, cal):
        r = runs.get(job)
        c = cell.get(job)
        out.append(
            SysTodayOut(
                job=job,
                planned_at=at,
                status=c.status if c else "pending",
                duration_s=c.duration_s if c else None,
                error=_first_line(r.error) if r else None,
            )
        )
    return out


def _file_doc(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _stamp(v: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _sources(settings: Settings, cal: TradingCalendar, today: date) -> list[SysSourceOut]:
    data_dir = settings.data_dir
    cutoff = today
    for _ in range(STALE_DAYS):
        cutoff = cal.previous_trading_day(cutoff)
    limit = datetime.combine(cutoff, time(0), ET)

    def state(t: datetime | None) -> str:
        if t is None:
            return "pending"
        return "ok" if t >= limit else "stale"

    out: list[SysSourceOut] = []
    files = [
        ("market", "首页市场（Massive）", snapshot_path(data_dir)),
        ("macro", "宏观（FRED / Coinbase）", macro_path(data_dir)),
        ("news", "新闻（Finnhub）", news_path(data_dir)),
        ("calendar", "财经日历（Forex Factory / FRED / Finnhub）", calendar_path(data_dir)),
    ]
    for key, label, path in files:
        doc = _file_doc(path) if path.exists() else None
        t = _stamp(doc.get("generated_at")) if doc else None
        detail = ""
        if key == "market" and doc:
            cov = doc.get("coverage")
            detail = f"截至 {doc.get('as_of')}" + (
                f"，覆盖 {cov:.0%}" if isinstance(cov, int | float) else ""
            )
        out.append(SysSourceOut(key=key, label=label, status=state(t), updated=t, detail=detail))
    stocks = market_dir(data_dir) / "stocks"
    docs = [d for p in stocks.glob("*.json") if (d := _file_doc(p))] if stocks.is_dir() else []
    times = [t for d in docs if (t := _stamp(d.get("news_updated")))]
    with_metrics = sum(1 for d in docs if d.get("metrics_updated"))
    newest = max(times) if times else None
    out.append(
        SysSourceOut(
            key="stocks",
            label="个股新闻与财务（Finnhub）",
            status=state(newest),
            updated=newest,
            detail=f"{len(docs)} 只，财务指标 {with_metrics} 只" if docs else "",
            progress=with_metrics / len(docs) if docs else None,
        )
    )
    return out


def _risk(s: Session, mode: str, settings: Settings, today: date) -> list[SysRiskOut]:
    r = settings.risk
    acct = s.scalars(
        select(AccountSnapshotRow)
        .where(AccountSnapshotRow.mode == mode)
        .order_by(AccountSnapshotRow.snapshot_date.desc())
        .limit(1)
    ).first()
    nl = float(acct.net_liquidation) if acct else None
    last = s.scalars(
        select(PositionSnapshot.snapshot_date)
        .where(PositionSnapshot.mode == mode)
        .order_by(PositionSnapshot.snapshot_date.desc())
        .limit(1)
    ).first()
    pos = (
        s.scalars(
            select(PositionSnapshot).where(
                PositionSnapshot.mode == mode, PositionSnapshot.snapshot_date == last
            )
        ).all()
        if last
        else []
    )
    mv = [float(p.market_value) for p in pos]
    start = datetime.combine(today, time(0), ET)
    fills = s.scalars(select(Fill).where(Fill.mode == mode, Fill.filled_at >= start)).all()
    traded = sum(float(f.price) * f.qty for f in fills)
    orders = s.scalars(select(Order).where(Order.mode == mode, Order.created_at >= start)).all()
    biggest = max(
        (float(o.limit_price or o.reference_price or 0) * o.qty for o in orders), default=None
    )
    since = datetime.combine(today - timedelta(days=30), time(0), ET)
    rejected = len(
        s.scalars(
            select(Order).where(
                Order.mode == mode, Order.status == "REJECTED", Order.created_at >= since
            )
        ).all()
    )
    scope = "白名单 + 标普 500 成分" if r.universe_whitelist == "sp500" else "白名单"
    if r.symbol_whitelist:
        scope += f"（{len(r.symbol_whitelist)} 只）"
    return [
        SysRiskOut(label="当日换手", used=traded / nl if nl else None, limit=r.max_daily_turnover),
        SysRiskOut(
            label="单只最大权重",
            used=max(mv) / nl if mv and nl else None,
            limit=r.max_symbol_weight,
        ),
        SysRiskOut(label="总仓位", used=sum(mv) / nl if nl else None, limit=r.max_total_weight),
        SysRiskOut(
            label="今日最大单笔",
            used=biggest,
            kind="usd",
            limit=float(r.max_order_usd) if r.max_order_usd else None,
        ),
        SysRiskOut(label="近 30 天被拒订单", used=float(rejected), kind="int"),
        SysRiskOut(label="标的范围", kind="text", text=scope),
    ]


def _upcoming(cal: TradingCalendar, today: date, n: int = 10) -> list[SysDayOut]:
    fomc = set(load_fomc_dates())
    days = [today]
    while len(days) < n:
        days.append(cal.next_trading_day(days[-1]))
    out = []
    d = today
    while d <= days[-1]:
        if cal.is_trading_day(d):
            tags = []
            if cal.is_month_end(d):
                tags.append("月末调仓")
            if d in fomc:
                tags.append("FOMC")
            if cal.is_early_close(d):
                tags.append(f"半日市 {cal.session_close(d).astimezone(ET):%H:%M} 收盘")
            out.append(SysDayOut(date=d, weekday=f"周{WEEK[d.weekday()]}", tags=tags))
        elif d.weekday() < 5:
            out.append(SysDayOut(date=d, weekday=f"周{WEEK[d.weekday()]}", tags=["休市"]))
        d += timedelta(days=1)
    return out


def _events(s: Session, mode: str, now: datetime) -> list[SysEventOut]:
    since = now - timedelta(days=30)
    out: list[SysEventOut] = []
    for a in s.scalars(
        select(JobAttempt).where(
            JobAttempt.mode == mode, JobAttempt.result == "failed", JobAttempt.started_at >= since
        )
    ):
        msg = _first_line(a.error) or "未知原因"
        out.append(
            SysEventOut(
                time=a.started_at,
                level="error",
                text=f"{a.job_name} {a.trade_date} 第 {a.attempt} 次失败：{msg}",
            )
        )
    flag = s.get(SystemFlag, "trading_enabled")
    if flag is not None and flag.updated_at and flag.updated_at >= since:
        on = flag.value == "true"
        out.append(
            SysEventOut(
                time=flag.updated_at,
                level="warn" if not on else "info",
                text=f"熔断开关改为{'开启' if on else '关闭'}",
            )
        )
    for b in s.scalars(
        select(BacktestRun).where(BacktestRun.status == "failed", BacktestRun.finished_at >= since)
    ):
        out.append(
            SysEventOut(
                time=b.finished_at or b.created_at,
                level="warn",
                text=f"回测 #{b.id} {b.strategy} 失败：{_first_line(b.error) or ''}",
            )
        )
    for o in s.scalars(
        select(Order).where(
            Order.mode == mode, Order.status == "REJECTED", Order.created_at >= since
        )
    ):
        out.append(
            SysEventOut(
                time=o.created_at,
                level="warn",
                text=f"订单被拒：{o.symbol} {o.side} {o.qty} 股（{o.reason or '无原因'}）",
            )
        )
    return sorted(out, key=lambda e: e.time, reverse=True)[:30]


def _queue(s: Session) -> SysQueueOut:
    runs = s.scalars(select(BacktestRun)).all()
    counts: dict[str, int] = defaultdict(int)
    for r in runs:
        counts[r.status] += 1
    cur = next((r for r in sorted(runs, key=lambda r: r.id) if r.status == "running"), None)
    done = [r.finished_at for r in runs if r.status == "done" and r.finished_at]
    return SysQueueOut(
        queued=counts["queued"],
        running=counts["running"],
        done=counts["done"],
        failed=counts["failed"],
        current_id=cur.id if cur else None,
        current_strategy=cur.strategy if cur else None,
        current_started=cur.started_at if cur else None,
        last_finished=max(done) if done else None,
    )


def _cards_and_banner(
    s: Session,
    mode: str,
    settings: Settings,
    grid: SysGridOut,
    timeline: list[SysTodayOut],
    today: date,
    sources: list[SysSourceOut],
    now: datetime,
) -> tuple[list[SysCardOut], SysBannerOut]:
    items: list[SysItemOut] = []
    for t in timeline:
        if t.status == "failed":
            items.append(
                SysItemOut(
                    level="error",
                    text=f"{t.job} {today} 失败：{t.error or ''}",
                    command=f"quant job {t.job} --date {today} --rerun-failed",
                )
            )
        elif t.status == "missing":
            items.append(SysItemOut(level="warn", text=f"{t.job} 已过计划时间仍未运行"))
    flag = s.get(SystemFlag, "trading_enabled")
    enabled = flag is not None and flag.value == "true"
    if mode in ("paper", "live") and not enabled:
        items.append(
            SysItemOut(
                level="warn",
                text="熔断开关关闭：不会下单（服务器上 quant trading enable --yes 开启）",
            )
        )
    for src in sources:
        if src.status == "stale":
            items.append(
                SysItemOut(level="warn", text=f"{src.label} 超过 {STALE_DAYS} 个交易日未更新")
            )
    week = now - timedelta(days=7)
    failed_bt = s.scalars(
        select(BacktestRun).where(BacktestRun.status == "failed", BacktestRun.finished_at >= week)
    ).all()
    if failed_bt:
        items.append(SysItemOut(level="warn", text=f"近 7 天有 {len(failed_bt)} 个回测失败"))
    level = "error" if any(i.level == "error" for i in items) else "warn" if items else "ok"
    planned = [t for t in timeline]
    done = sum(1 for t in planned if t.status in ("success", "retry_success"))
    last_exec = s.scalars(
        select(JobRun)
        .where(JobRun.mode == mode, JobRun.job_name == "daily_execute")
        .order_by(JobRun.trade_date.desc())
        .limit(1)
    ).first()
    broker_ok = s.scalars(
        select(JobRun)
        .where(
            JobRun.mode == mode,
            JobRun.status == "success",
            JobRun.job_name.in_(("daily_execute", "daily_report")),
        )
        .order_by(JobRun.finished_at.desc())
        .limit(1)
    ).first()
    mode_text = {"sim": "本地模拟 SIM", "paper": "模拟盘 PAPER", "live": "实盘 LIVE"}.get(
        mode, mode
    )
    cards = [
        SysCardOut(key="mode", label="交易模式", value=mode_text, sub=f"Broker：{settings.broker}"),
        SysCardOut(
            key="kill_switch",
            label="熔断开关",
            value="开启（允许下单）" if enabled else "关闭（禁止下单）",
            sub=f"修改于 {flag.updated_at.astimezone(ET):%m-%d %H:%M}"
            if flag and flag.updated_at
            else "",
            level="ok" if enabled else "warn",
        ),
        SysCardOut(
            key="broker",
            label="券商连接",
            value="本地模拟"
            if mode == "sim"
            else (
                f"{broker_ok.finished_at.astimezone(ET):%m-%d %H:%M} 成功"
                if broker_ok and broker_ok.finished_at
                else "暂无记录"
            ),
            sub="最近一次成功的执行 / 报告任务",
            level="ok" if mode == "sim" or broker_ok else "warn",
        ),
        SysCardOut(
            key="reconcile",
            label="持仓对账",
            value=("已对账" if last_exec.status == "success" else "失败")
            if last_exec
            else "暂无记录",
            sub=(
                f"{last_exec.trade_date}"
                + (f"：{_first_line(last_exec.error)}" if last_exec.error else "")
            )
            if last_exec
            else "",
            level=("ok" if last_exec.status == "success" else "error") if last_exec else "info",
        ),
        SysCardOut(
            key="today_jobs",
            label="今日任务",
            value=f"{done} / {len(planned)}",
            sub=f"{today}（美东）",
            level="ok" if done == len(planned) else "warn" if planned else "info",
        ),
        SysCardOut(
            key="data",
            label="数据完整度",
            value=f"{sum(1 for x in sources if x.status == 'ok')} / {len(sources)}",
            sub="正常的数据源",
            level="ok" if all(x.status == "ok" for x in sources) else "warn",
        ),
    ]
    return cards, SysBannerOut(level=level, items=items)


def overview(
    s: Session | None, mode: str, settings: Settings, cal: TradingCalendar
) -> SystemOverview:
    now = _now()
    today = _today(cal, now)
    sources = _sources(settings, cal, today)
    upcoming = _upcoming(cal, today)
    if s is None:
        return SystemOverview(
            mode=mode,
            today=today,
            sources=sources,
            upcoming=upcoming,
            banner=SysBannerOut(level="warn", items=[SysItemOut(level="warn", text=NO_DB)]),
        )
    days = cal.trading_days(today - timedelta(days=60), today)[-GRID_DAYS:]
    grid = _grid(s, mode, days, cal, settings, now)
    timeline = _timeline(grid, s, mode, today, cal, settings)
    cards, banner = _cards_and_banner(s, mode, settings, grid, timeline, today, sources, now)
    res = resources(s, now)
    if res.items:
        banner.items = [*banner.items, *res.items]
        banner.level = "error" if banner.level == "error" else "warn"
    return SystemOverview(
        mode=mode,
        today=today,
        banner=banner,
        cards=cards,
        grid=grid,
        timeline=timeline,
        sources=sources,
        risk=_risk(s, mode, settings, today),
        upcoming=upcoming,
        events=_events(s, mode, now),
        queue=_queue(s),
        resources=res,
    )


def job_attempts(s: Session, mode: str, job: str, day: date) -> list[SysAttemptOut]:
    rows = s.scalars(
        select(JobAttempt)
        .where(JobAttempt.mode == mode, JobAttempt.job_name == job, JobAttempt.trade_date == day)
        .order_by(JobAttempt.attempt)
    ).all()
    return [
        SysAttemptOut(
            attempt=a.attempt,
            started_at=a.started_at,
            finished_at=a.finished_at,
            duration_s=_secs(a.started_at, a.finished_at),
            result=a.result,
            error="\n".join(a.error.splitlines()[:20]) if a.error else None,
        )
        for a in rows
    ]
