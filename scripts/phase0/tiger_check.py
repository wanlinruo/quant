"""Phase 0 任务 0.1：老虎 OpenAPI 连通性验证。

依次执行：读取配置 → 查询账户 → 查询持仓 → SPY 报价 → SPY 历史日线
→（仅模拟账户）远离市价的 1 股限价买单 → 查询订单状态 → 撤单。
任一步失败打印错误并继续后续可执行的步骤。

环境变量：
    TIGER_PROPS_PATH  tiger_openapi_config.properties 所在目录（权限 600，不在仓库内）
    TRADING_MODE      只有为 paper 且账户为模拟账户时才执行下单步骤

用法：
    uv run --env-file .env python scripts/phase0/tiger_check.py

接口用法参照官方文档：https://docs.itigerup.com/docs/prepare
"""

from __future__ import annotations

import math
import os
import re
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYMBOL = "SPY"
# 买单限价 = 参考价 × 该比例，远离市价，确保不会成交
FAR_LIMIT_RATIO = 0.5
TERMINAL_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}

# 模拟资金账号为 17 位数字（见官方文档“注册开发者信息”）
_PAPER_ACCOUNT_RE = re.compile(r"\d{17}")


def is_paper_account(account: str | None) -> bool:
    return bool(account) and _PAPER_ACCOUNT_RE.fullmatch(str(account)) is not None


def mask_account(account: str | None) -> str:
    s = str(account or "")
    return f"***{s[-4:]}" if len(s) > 4 else "***"


def order_permission(trading_mode: str | None, account: str | None) -> tuple[bool, str]:
    """下单前的安全检查：TRADING_MODE 必须为 paper，且账户必须是模拟账户。"""
    if trading_mode != "paper":
        return False, f"TRADING_MODE={trading_mode!r}，只有 paper 才允许下单"
    if not is_paper_account(account):
        return False, f"账户 {mask_account(account)} 不是模拟账户（17 位数字），拒绝下单"
    return True, "模拟账户，允许下单"


def status_name(status: Any) -> str:
    """订单状态名：SDK 返回 OrderStatus 枚举，取 .name；兼容字符串。"""
    return str(getattr(status, "name", status))


def far_limit_price(reference_price: float) -> float:
    """远低于参考价的买入限价，向下取整到分。"""
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError(f"无效参考价：{reference_price}")
    return max(math.floor(reference_price * FAR_LIMIT_RATIO * 100) / 100, 0.01)


class SkipStep(Exception):
    """前置步骤未完成，本步骤无法执行。"""


@dataclass
class State:
    config: Any = None
    quote_client: Any = None
    trade_client: Any = None
    reference_price: float | None = None
    order_id: int | None = None
    results: list[tuple[str, str]] = field(default_factory=list)


def _load_config(st: State) -> None:
    from tigeropen.quote.quote_client import QuoteClient
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient

    props = os.environ.get("TIGER_PROPS_PATH")
    if not props:
        raise RuntimeError("未设置 TIGER_PROPS_PATH")
    props_dir = Path(props).expanduser()
    props_file = props_dir / "tiger_openapi_config.properties"
    if not props_file.is_file():
        raise RuntimeError(f"找不到配置文件：{props_file}")
    mode = props_file.stat().st_mode & 0o777
    if mode & 0o077:
        print(f"  警告：配置文件权限为 {oct(mode)}，建议 chmod 600")

    st.config = TigerOpenClientConfig(props_path=str(props_dir) + os.sep)
    st.quote_client = QuoteClient(st.config)
    st.trade_client = TradeClient(st.config)
    print(
        f"  account: {mask_account(st.config.account)}"
        f"（{'模拟' if is_paper_account(st.config.account) else '非模拟'}）"
    )
    print(f"  license: {st.config.license}")
    print(f"  TRADING_MODE: {os.environ.get('TRADING_MODE')!r}")


def _query_account(st: State) -> None:
    pa = st.trade_client.get_prime_assets(base_currency="USD")
    seg = pa.segments["S"]
    print(f"  currency: {seg.currency}  capability: {seg.capability}")
    print(f"  net_liquidation: {seg.net_liquidation}")
    print(f"  cash_balance: {seg.cash_balance}")
    print(f"  buying_power: {seg.buying_power}")


def _query_positions(st: State) -> None:
    from tigeropen.common.consts import SecurityType

    positions = st.trade_client.get_positions(sec_type=SecurityType.STK)
    print(f"  持仓数: {len(positions)}")
    for p in positions:
        print(
            f"  {p.contract.symbol}: qty={p.position_qty} avg_cost={p.average_cost}"
            f" market_value={p.market_value}"
        )


def _quote(st: State) -> None:
    try:
        df = st.quote_client.get_stock_briefs([SYMBOL])
        row = df.iloc[0]
        print("  实时行情 get_stock_briefs: OK")
        print(
            f"  latest={row['latest_price']} bid={row['bid_price']} ask={row['ask_price']}"
            f" time={row['latest_time']}"
        )
        st.reference_price = float(row["latest_price"])
        return
    except Exception as e:
        print(f"  实时行情 get_stock_briefs 失败：{type(e).__name__}: {e}")
        print("  改用免费延迟行情 get_stock_delay_briefs（约 15 分钟延迟）")
    df = st.quote_client.get_stock_delay_briefs([SYMBOL])
    row = df.iloc[0]
    print(f"  延迟行情: close={row['close']} pre_close={row['pre_close']} time={row['time']}")
    st.reference_price = float(row["close"])


def _bars(st: State) -> None:
    from tigeropen.common.consts import BarPeriod

    df = st.quote_client.get_bars([SYMBOL], period=BarPeriod.DAY, limit=5)
    print(df[["symbol", "time", "open", "high", "low", "close", "volume"]].to_string(index=False))
    if st.reference_price is None and len(df):
        st.reference_price = float(df["close"].iloc[-1])


def _place_order(st: State) -> None:
    from tigeropen.common.util.contract_utils import stock_contract
    from tigeropen.common.util.order_utils import limit_order

    allowed, reason = order_permission(os.environ.get("TRADING_MODE"), st.config.account)
    if not allowed:
        raise PermissionError(reason)
    if st.reference_price is None:
        raise RuntimeError("没有参考价，无法计算远离市价的限价")
    price = far_limit_price(st.reference_price)
    contract = stock_contract(symbol=SYMBOL, currency="USD")
    order = limit_order(
        account=st.config.account, contract=contract, action="BUY", limit_price=price, quantity=1
    )
    st.order_id = st.trade_client.place_order(order)
    print(f"  已提交：BUY 1 {SYMBOL} LMT {price}（参考价 {st.reference_price}）")
    print(f"  order id: {st.order_id}  status: {status_name(order.status)}")


def _print_order(order: Any) -> None:
    print(
        f"  status={status_name(order.status)} filled={order.filled} remaining={order.remaining}"
        f" limit_price={order.limit_price} reason={order.reason!r}"
    )


def _get_order(st: State) -> None:
    if st.order_id is None:
        raise SkipStep("没有已提交的订单")
    order = st.trade_client.get_order(id=st.order_id)
    _print_order(order)


def _cancel_order(st: State) -> None:
    if st.order_id is None:
        raise SkipStep("没有已提交的订单")
    order = st.trade_client.get_order(id=st.order_id)
    if status_name(order.status) in TERMINAL_STATUSES:
        print(f"  订单已是终态 {status_name(order.status)}，无需撤单")
        return
    st.trade_client.cancel_order(id=st.order_id)
    print("  撤单请求已提交，等待确认…")
    for _ in range(10):
        time.sleep(1)
        order = st.trade_client.get_order(id=st.order_id)
        if status_name(order.status) in TERMINAL_STATUSES:
            break
    _print_order(order)
    if status_name(order.status) != "CANCELLED":
        raise RuntimeError(f"撤单未确认，当前状态 {status_name(order.status)}，请在 APP 中人工检查")


STEPS: list[tuple[str, Callable[[State], None], bool]] = [
    # (名称, 函数, 是否依赖配置加载成功)
    ("读取配置", _load_config, False),
    ("查询账户", _query_account, True),
    ("查询持仓", _query_positions, True),
    (f"{SYMBOL} 报价", _quote, True),
    (f"{SYMBOL} 历史日线", _bars, True),
    ("模拟账户限价买单", _place_order, True),
    ("查询订单状态", _get_order, True),
    ("撤单", _cancel_order, True),
]


def _redact(text: str, account: str | None) -> str:
    if account:
        text = text.replace(str(account), mask_account(account))
    return text


def main() -> int:
    st = State()
    for i, (name, fn, needs_config) in enumerate(STEPS, 1):
        print(f"\n[{i}/{len(STEPS)}] {name}")
        if needs_config and st.config is None:
            print("  跳过：配置未加载")
            st.results.append((name, "SKIP"))
            continue
        try:
            fn(st)
            st.results.append((name, "OK"))
        except SkipStep as e:
            print(f"  跳过：{e}")
            st.results.append((name, "SKIP"))
        except PermissionError as e:
            print(f"  拒绝：{e}")
            st.results.append((name, "REFUSED"))
        except Exception as e:
            account = st.config.account if st.config else None
            print(f"  失败：{_redact(f'{type(e).__name__}: {e}', account)}")
            if os.environ.get("TIGER_CHECK_DEBUG"):
                print(_redact(traceback.format_exc(), account))
            st.results.append((name, "FAIL"))

    print("\n==== 汇总 ====")
    for name, status in st.results:
        print(f"  {status:8s} {name}")
    if st.order_id is not None and ("撤单", "OK") not in st.results:
        print(f"\n!! 订单 {st.order_id} 可能仍未撤销，请在老虎 APP 中检查模拟账户")
    return 0 if all(s == "OK" for _, s in st.results) else 1


if __name__ == "__main__":
    sys.exit(main())
