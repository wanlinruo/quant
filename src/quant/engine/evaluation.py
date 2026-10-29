"""策略是否“成功”的判定（swing_pullback 设计第 7 节，用户 2026-10-29 确认的标准）。

样本外须同时满足：夏普 > QQQ 且 ≥ 等权持有同组个股；最大回撤 ≤ QQQ 的 60%；年化 ≥ QQQ 的 80%。
数值按字面比较（回撤为负数；QQQ 年化为负时同样按 0.8 倍比较）。任一指标为 NaN 即不通过。
"""

from __future__ import annotations

import math
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
