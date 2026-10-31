"""小资金测算脚本的碎股等价放大（#55）。"""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from quant.broker.sim import CostModel
from quant.engine.orders import OrderPlanParams

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research" / "small_capital.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("small_capital_research", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["small_capital_research"] = module
    spec.loader.exec_module(module)
    return module


R = _load()
COSTS = CostModel(commission_per_share=Decimal("0.0039"), min_commission=Decimal(0),
                  platform_per_share=Decimal("0.004"), platform_min=Decimal(1),
                  max_fee_pct=Decimal("0.005"))  # fmt: skip


def test_scaled_fee_equals_real_fee_times_k() -> None:
    # 真实：买 2.5 股（碎股）、成交额 500 美元 → 佣金 0.00975 + 平台费 max(0.01, 1) = 1.01
    k = Decimal(1000)
    costs, plan = R.scaled(COSTS, OrderPlanParams(min_trade_usd=Decimal(100)), k)
    real = COSTS.commission_per_share * Decimal("2.5") + COSTS.platform_min
    assert costs.commission(2500, Decimal(500) * k) == (real * k).quantize(Decimal("0.01"))
    assert plan.min_trade_usd == Decimal(100000)
    assert costs.slippage == COSTS.slippage and costs.max_fee_pct == COSTS.max_fee_pct


def test_scaled_fee_cap_is_scale_invariant() -> None:
    # 成交额很小（100 美元）时平台费封顶 0.5% = 0.5 美元
    k = Decimal(1000)
    costs, _ = R.scaled(COSTS, OrderPlanParams(), k)
    # 真实 1 股：佣金 0.0039 + 平台费 min(1, 0.5) = 0.5039（真实按分取整为 0.50，放大后不丢分位）
    real = COSTS.commission_per_share + Decimal(100) * COSTS.max_fee_pct  # type: ignore[operator]
    assert costs.commission(1000, Decimal(100) * k) == real * k
