"""回测稳健性标签（#68，设计 5.3、7.3）：父回测与子回测 → 热力图、相邻参数、walk-forward、
成本与起始时间敏感性。只读。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant.api.queries import robust_progress
from quant.api.schemas import (
    RbCellOut,
    RbGridOut,
    RbMetricsOut,
    RbRowOut,
    RbWfOut,
    RbWfSegOut,
    RobustnessOut,
)
from quant.db.models import BacktestEquity, BacktestRun
from quant.engine import robustness
from quant.engine.evaluation import NEIGHBOR_MAX_DRAWDOWN, NEIGHBOR_MIN_SHARPE
from quant.engine.metrics import compute_metrics

NOT_REQUESTED = "未运行：新建回测时勾选“同时跑稳健性检验”，worker 会在回测完成后自动排队一组子回测"
BASE_LABEL = "本次"
FINISHED = ("done", "failed")


def _metrics(m: dict[str, Any] | None) -> RbMetricsOut | None:
    if not m:
        return None
    return RbMetricsOut(
        cagr=m.get("cagr"), sharpe=m.get("sharpe"), max_drawdown=m.get("max_drawdown")
    )


def _passes(m: RbMetricsOut | None) -> bool | None:
    if m is None or m.sharpe is None or m.max_drawdown is None:
        return None
    return m.max_drawdown >= NEIGHBOR_MAX_DRAWDOWN and m.sharpe >= NEIGHBOR_MIN_SHARPE


def _row(r: BacktestRun, label: str | None = None, base: bool = False) -> RbRowOut:
    v = r.variant or {}
    return RbRowOut(
        label=label or v.get("label", ""),
        run_id=r.id,
        status=r.status,
        metrics=_metrics(r.metrics) if r.status == "done" else None,
        base=base,
    )


def _err(r: BacktestRun) -> str | None:
    return (
        r.error.strip().splitlines()[0]
        if r.status == "failed" and r.error and r.error.strip()
        else None
    )


def _equity(s: Session, ids: list[int]) -> dict[int, pd.Series]:
    out: dict[int, dict[Any, float]] = {}
    for p in s.scalars(
        select(BacktestEquity)
        .where(BacktestEquity.run_id.in_(ids))
        .order_by(BacktestEquity.run_id, BacktestEquity.equity_date)
    ):
        out.setdefault(p.run_id, {})[pd.Timestamp(p.equity_date)] = float(p.equity)
    return {k: pd.Series(v) for k, v in out.items()}


def _wf_metrics(eq: pd.Series) -> RbMetricsOut:
    m = compute_metrics(eq).to_dict()
    return _metrics(m) or RbMetricsOut()


def _walk_forward(s: Session, parent: BacktestRun, grid: list[BacktestRun]) -> RbWfOut:
    out = RbWfOut(train_years=robustness.TRAIN_YEARS)
    done = [r for r in grid if r.status == "done"]
    failed = len(grid) - len(done)
    if len(done) + 1 < 2:
        out.note = "参数网格的有效格子不足 2 个（子回测失败），不计算"
        return out
    eq = _equity(s, [parent.id, *(r.id for r in done)])
    labels = {
        parent.id: BASE_LABEL,
        **{r.id: (r.variant or {}).get("label", str(r.id)) for r in done},
    }
    series = {labels[k]: v for k, v in eq.items()}
    wf = robustness.walk_forward(series, BASE_LABEL)
    if wf is None:
        n = robustness.TRAIN_YEARS
        out.note = f"区间不足 {n + 1} 年（训练 {n} 年 + 测试 1 年）"
        return out
    out.segments = [RbWfSegOut(**vars(x)) for x in wf.segments]
    out.strategy, out.base = _wf_metrics(wf.oos), _wf_metrics(wf.base)
    out.note = f"每年用前 {robustness.TRAIN_YEARS} 年夏普最高的参数，拼接各测试年的收益"
    if failed:
        out.note += f"；排除 {failed} 个失败的格子"
    return out


def _neighbors(kids: list[BacktestRun], combo: bool) -> tuple[list[RbRowOut], bool | None, str]:
    """相邻参数逐个检查：行、判定（只有组合策略有阈值，全部完成才判定）、说明。"""
    rows = [_row(k) for k in kids if (k.variant or {}).get("neighbor")]
    if not combo:
        return rows, None, "验收阈值只针对组合策略；这里列出各相邻参数相对本次的变化"
    for r in rows:
        r.passed = _passes(r.metrics) if r.status == "done" else None
    unfinished = [r for r in rows if r.status != "done"]
    if unfinished:
        return rows, None, f"不完整：{len(unfinished)} 项未完成或失败"
    if not rows:
        return rows, None, ""
    note = f"每项回撤 ≥ {NEIGHBOR_MAX_DRAWDOWN:.0%} 且夏普 ≥ {NEIGHBOR_MIN_SHARPE}"
    return rows, all(bool(r.passed) for r in rows), note


def _children(s: Session, run_id: int) -> list[BacktestRun]:
    q = select(BacktestRun).where(BacktestRun.parent_id == run_id).order_by(BacktestRun.id)
    return list(s.scalars(q))


def robustness_view(s: Session, run_id: int) -> RobustnessOut | None:
    parent = s.get(BacktestRun, run_id)
    if parent is None:
        return None
    if not parent.params.get(robustness.ROBUST_KEY):
        return RobustnessOut(status="not_requested", note=NOT_REQUESTED)
    if parent.status == "failed":
        return RobustnessOut(status="not_requested", note="本次回测失败，没有生成子回测")
    kids = _children(s, run_id)
    progress = robust_progress(s, [run_id]).get(run_id)
    pending = parent.status != "done" or any(k.status not in FINISHED for k in kids)
    out = RobustnessOut(status="pending" if pending else "done", progress=progress)
    if parent.status != "done":
        out.note = "等待本次回测完成后生成子回测"
        return out
    if not kids:
        out.note = "没有生成子回测（查看 backtest-worker 日志）"
        out.status = "done"
        return out
    by_kind: dict[str, list[BacktestRun]] = {}
    for k in kids:
        by_kind.setdefault((k.variant or {}).get("kind", ""), []).append(k)
    base_metrics = _metrics(parent.metrics)
    combo = parent.strategy == "combo"

    if robustness.supported(parent.strategy):
        (xl, xs), (yl, ys) = robustness.grid_axes(parent.strategy, parent.params)
        cells = {
            ((k.variant or {})["x"], (k.variant or {})["y"]): k for k in by_kind.get("grid", [])
        }
        grid_cells = []
        for y in range(3):
            for x in range(3):
                label = f"{xl} {xs[x]} · {yl} {ys[y]}"
                if (x, y) == (1, 1):
                    grid_cells.append(
                        RbCellOut(
                            x=x,
                            y=y,
                            label=label,
                            run_id=parent.id,
                            status="done",
                            metrics=base_metrics,
                            center=True,
                        )
                    )
                elif (cr := cells.get((x, y))) is not None:
                    grid_cells.append(
                        RbCellOut(
                            x=x,
                            y=y,
                            label=label,
                            run_id=cr.id,
                            status=cr.status,
                            metrics=_metrics(cr.metrics) if cr.status == "done" else None,
                            error=_err(cr),
                        )
                    )
                else:
                    grid_cells.append(
                        RbCellOut(
                            x=x,
                            y=y,
                            label=label,
                            status="skipped",
                            error="参数越界，未运行",
                        )
                    )
        out.grid = RbGridOut(x_label=xl, y_label=yl, x_values=xs, y_values=ys, cells=grid_cells)

    out.neighbors, out.neighbors_verdict, out.neighbors_note = _neighbors(kids, combo)
    skipped_neighbors = robustness.skipped_neighbor_labels(parent.strategy, parent.params)
    if skipped_neighbors:
        prefix = f"{out.neighbors_note}；" if out.neighbors_note else ""
        out.neighbors_note = f"{prefix}参数越界未运行（不计入判定）：{'、'.join(skipped_neighbors)}"

    grid = by_kind.get("grid", [])
    if not any(k.status not in FINISHED for k in grid):
        out.walk_forward = _walk_forward(s, parent, grid)

    base_row = RbRowOut(
        label="成本 ×1（本次）", run_id=parent.id, status="done", metrics=base_metrics, base=True
    )
    costs = sorted(by_kind.get("cost", []), key=lambda k: (k.variant or {}).get("cost_mult") or 0)
    out.costs = [_row(k) for k in costs if ((k.variant or {}).get("cost_mult") or 0) < 1]
    out.costs += [base_row] + [
        _row(k) for k in costs if ((k.variant or {}).get("cost_mult") or 0) > 1
    ]
    out.starts = [
        RbRowOut(
            label=f"起点 {parent.start}（本次）",
            run_id=parent.id,
            status="done",
            metrics=base_metrics,
            base=True,
        )
    ]
    out.starts += [_row(k) for k in by_kind.get("start", [])]
    return out


def neighbor_verdict(s: Session, run_id: int) -> bool | None:
    """概览“相邻设置”验收项：子回测全部完成时的判定，否则 None。"""
    parent = s.get(BacktestRun, run_id)
    if parent is None or parent.status != "done" or not parent.params.get(robustness.ROBUST_KEY):
        return None
    return _neighbors(_children(s, run_id), parent.strategy == "combo")[1]
