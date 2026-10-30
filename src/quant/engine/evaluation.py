"""策略是否“成功”的判定（swing_pullback 设计第 7 节，用户 2026-10-29 确认的标准）。

样本外须同时满足：夏普 > QQQ 且 ≥ 等权持有同组个股；最大回撤 ≤ QQQ 的 60%；年化 ≥ QQQ 的 80%。
数值按字面比较（回撤为负数；QQQ 年化为负时同样按 0.8 倍比较）。任一指标为 NaN 即不通过。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from quant.engine.metrics import Metrics

DRAWDOWN_RATIO = 0.6
CAGR_RATIO = 0.8


@dataclass(frozen=True)
class Criteria:
    sharpe_vs_qqq: bool
    sharpe_vs_equal: bool
    drawdown: bool
    cagr: bool

    @property
    def passed(self) -> bool:
        return self.sharpe_vs_qqq and self.sharpe_vs_equal and self.drawdown and self.cagr


def _ge(a: float, b: float) -> bool:
    """a ≥ b，边界上容忍浮点误差（如 0.2 × 0.8 = 0.16000000000000003）。"""
    return a >= b or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def meets_criteria(strategy: Metrics, qqq: Metrics, equal: Metrics) -> Criteria:
    s = strategy
    return Criteria(
        sharpe_vs_qqq=s.sharpe > qqq.sharpe,
        sharpe_vs_equal=_ge(s.sharpe, equal.sharpe),
        drawdown=_ge(s.max_drawdown, qqq.max_drawdown * DRAWDOWN_RATIO),
        cagr=_ge(s.cagr, qqq.cagr * CAGR_RATIO),
    )


# ---- 组合策略（#47，设计第 7 节） ----

COMBO_MAX_DRAWDOWN = -0.25
COMBO_EXCESS_CAGR = 0.03
COMBO_MIN_SHARPE = 1.1
NEIGHBOR_MAX_DRAWDOWN = -0.30
NEIGHBOR_MIN_SHARPE = 1.0


def combo_checks(
    full: Metrics,
    qqq_full: Metrics,
    ins: Metrics,
    qqq_ins: Metrics,
    oos: Metrics,
    qqq_oos: Metrics,
    neighbors: Sequence[Metrics],
) -> dict[str, bool]:
    """逐项判定（全区间、扣除成本）。neighbors 为相邻设置的全区间指标；为空视为不满足。"""
    robust = bool(neighbors) and all(
        _ge(n.max_drawdown, NEIGHBOR_MAX_DRAWDOWN) and _ge(n.sharpe, NEIGHBOR_MIN_SHARPE)
        for n in neighbors
    )
    return {
        "最大回撤 ≥ -25%": _ge(full.max_drawdown, COMBO_MAX_DRAWDOWN),
        "年化 ≥ QQQ + 3 个百分点": _ge(full.cagr, qqq_full.cagr + COMBO_EXCESS_CAGR),
        "夏普 ≥ 1.1": _ge(full.sharpe, COMBO_MIN_SHARPE),
        "两个区间夏普都高于 QQQ": ins.sharpe > qqq_ins.sharpe and oos.sharpe > qqq_oos.sharpe,
        "相邻设置：回撤 ≥ -30% 且夏普 ≥ 1.0": robust,
    }
