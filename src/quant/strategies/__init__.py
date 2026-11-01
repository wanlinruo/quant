"""策略：只输出目标仓位权重，纯计算、无 I/O。"""

from __future__ import annotations

from quant.strategies.base import Strategy
from quant.strategies.combo import Combo
from quant.strategies.etf_momentum import EtfMomentum
from quant.strategies.momentum_select import MomentumSelect
from quant.strategies.swing_pullback import SwingPullback

STRATEGIES: dict[str, type[Strategy]] = {
    Combo.name: Combo,
    EtfMomentum.name: EtfMomentum,
    MomentumSelect.name: MomentumSelect,
    SwingPullback.name: SwingPullback,
}


# 面板回测表单可选的 ETF 标的池；服务器回测数据也要覆盖这些（#66）
ETF_OPTIONS: tuple[str, ...] = ("SPY", "QQQ", "TLT", "GLD", "IWM", "EFA", "EEM", "VNQ", "DBC")


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(f"未知策略：{name}，可选：{', '.join(sorted(STRATEGIES))}") from None
