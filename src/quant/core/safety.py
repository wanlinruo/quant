"""下单安全检查：所有向券商提交订单的路径都必须先调用 check_order_allowed。"""

from __future__ import annotations

import re

from quant.core.settings import TradingMode, is_dev_machine

# 老虎模拟资金账号为 17 位数字（官方文档“注册开发者信息”）
_PAPER_ACCOUNT_RE = re.compile(r"\d{17}")


class OrderNotAllowedError(PermissionError):
    """当前交易模式或账户不允许提交订单。"""


def is_paper_account(account: str | None) -> bool:
    return bool(account) and _PAPER_ACCOUNT_RE.fullmatch(str(account)) is not None


def mask_account(account: str | None) -> str:
    """账户号脱敏，只保留后 4 位。日志、异常信息中一律使用。"""
    s = str(account or "")
    return f"***{s[-4:]}" if len(s) > 4 else "***"


def check_order_allowed(trading_mode: TradingMode, broker: str, account: str | None) -> None:
    """不允许下单时抛出 OrderNotAllowedError。

    - broker=sim（回测撮合，不连券商）：任何模式都允许。
    - broker=tiger：sim 模式拒绝；paper 模式只允许模拟账户；
      live 模式只允许非模拟账户，且不能在开发机（Mac）上运行。
    """
    if broker == "sim":
        return
    if broker != "tiger":
        raise OrderNotAllowedError(f"未知 broker：{broker!r}")
    masked = mask_account(account)
    if trading_mode == "sim":
        raise OrderNotAllowedError("TRADING_MODE=sim，禁止向券商下单")
    if trading_mode == "paper":
        if not is_paper_account(account):
            raise OrderNotAllowedError(f"TRADING_MODE=paper，但账户 {masked} 不是模拟账户")
        return
    if trading_mode == "live":
        if is_dev_machine():
            raise OrderNotAllowedError("开发机禁止实盘下单")
        if not account or is_paper_account(account):
            raise OrderNotAllowedError(f"TRADING_MODE=live，但账户 {masked} 不是实盘账户")
        return
    raise OrderNotAllowedError(f"未知交易模式：{trading_mode!r}")
