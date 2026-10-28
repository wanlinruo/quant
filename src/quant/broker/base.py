"""Broker 抽象接口（PROJECT_PLAN 6.1）。

实现：TigerBroker（模拟/实盘由配置切换）、SimBroker（回测撮合）。
接口只使用 quant.core.models 中的领域模型，不暴露任何券商 SDK 类型。
place_order 的实现必须先调用 quant.core.safety.check_order_allowed。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from quant.core.models import AccountSnapshot, OrderRequest, OrderStatus, Position, Quote


class Broker(ABC):
    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        """现金、净值、购买力。"""

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """当前持仓，键为代码；无持仓的标的不出现。"""

    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """最新报价；拿不到报价的标的不出现在结果中，由调用方决定如何处理。"""

    @abstractmethod
    def place_order(self, req: OrderRequest) -> str:
        """提交订单，返回券商订单号。"""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """请求撤单；是否撤销成功以 get_order 返回的状态为准。"""

    @abstractmethod
    def get_order(self, order_id: str) -> OrderStatus: ...

    @abstractmethod
    def get_open_orders(self) -> list[OrderStatus]:
        """未到终态的订单。"""
