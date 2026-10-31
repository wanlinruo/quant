"""面板接口的数据模型。前端类型由这里生成（python -m quant.api.openapi）。

不得包含数据库地址、密钥、账户号等字段。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

Mode = Literal["sim", "paper", "live"]
MODES: tuple[Mode, ...] = ("sim", "paper", "live")
MODE_LABELS: dict[Mode, str] = {"sim": "本地模拟", "paper": "模拟盘", "live": "实盘"}


class AccountInfo(BaseModel):
    mode: Mode
    label: str
    has_data: bool


class Meta(BaseModel):
    trading_mode: Mode
    broker: str
    database_configured: bool
    accounts: list[AccountInfo]  # 固定按 sim、paper、live 顺序
    default_account: Mode
    data_as_of: datetime | None


class Position(BaseModel):
    snapshot_date: date
    symbol: str
    qty: int
    avg_cost: float
    market_value: float


class AccountPoint(BaseModel):
    date: date
    cash: float
    net_liquidation: float


class SignalOut(BaseModel):
    strategy: str
    signal_date: date
    data_as_of: date
    target_weights: dict[str, float]


class OrderOut(BaseModel):
    created_at: datetime
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    qty: int
    limit_price: float | None
    reference_price: float | None
    status: str
    filled_qty: int
    avg_fill_price: float | None
    reason: str


class FillOut(BaseModel):
    filled_at: datetime
    symbol: str
    side: str
    qty: int
    price: float
    commission: float


class BacktestRunOut(BaseModel):
    id: int
    strategy: str
    status: str
    start: date
    end: date
    params: dict[str, Any]
    costs: dict[str, str] | None
    metrics: dict[str, Any]
    error: str
    created_at: datetime
    finished_at: datetime | None


class EquityPoint(BaseModel):
    run_id: int
    date: date
    equity: float
    drawdown: float


class JobStatusOut(BaseModel):
    job_name: str
    trade_date: date
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str


class FailureOut(BaseModel):
    time: datetime
    source: str
    message: str


class QueueStatus(BaseModel):
    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0


class DataStatusRow(BaseModel):
    symbol: str
    last_date: date | None
    rows: int
    sources: str
    last_ingested: datetime | None
    ok: bool
    issues: str


class Bar(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: float


class CostsIn(BaseModel):
    commission_per_share: Decimal | None
    min_commission: Decimal | None
    slippage: Decimal


class BacktestSubmit(BaseModel):
    strategy: str
    start: date
    end: date
    universe: list[str]
    safe_asset: str
    lookback: int
    top_k: int
    sma_window: int
    cash: Decimal
    costs: CostsIn


class SubmitResult(BaseModel):
    id: int


class BacktestDefaults(BaseModel):
    """提交表单的默认值（与改版前的面板表单一致）。"""

    strategies: list[str]
    universe_options: list[str]
    universe: list[str]
    safe_asset: str
    lookback: int
    top_k: int
    sma_window: int
    cash: Decimal
    commission_per_share: str | None
    min_commission: str | None
    slippage: str
    start: date
    end: date
