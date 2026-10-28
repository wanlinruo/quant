"""策略：只输出目标仓位权重，纯计算、无 I/O。"""

from __future__ import annotations

from quant.strategies.base import Strategy
from quant.strategies.etf_momentum import EtfMomentum

STRATEGIES: dict[str, type[Strategy]] = {EtfMomentum.name: EtfMomentum}


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(f"未知策略：{name}，可选：{', '.join(sorted(STRATEGIES))}") from None
