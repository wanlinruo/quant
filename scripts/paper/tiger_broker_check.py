"""TigerBroker 模拟账户集成检查（#29）：账户、持仓、延迟报价、远离市价的 1 股限价单、查询、撤单。

只允许 TRADING_MODE=paper 且账户为模拟账户（TigerBroker 构造与下单时都会检查）。
用法：
    TRADING_MODE=paper uv run --env-file .env python scripts/paper/tiger_broker_check.py
"""

import time
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from quant.broker.tiger import TigerBroker
from quant.core.models import OrderRequest, Side, make_client_order_id
from quant.core.settings import load_settings

s = load_settings()
if s.trading_mode != "paper":
    raise SystemExit("只允许在 TRADING_MODE=paper 下运行")
b = TigerBroker.from_settings(s)
a = b.get_account()
print("account", b.masked_account, "NAV", a.net_liquidation, "cash", a.cash)
print("positions", {k: v.qty for k, v in b.get_positions().items()})
q = b.get_quotes(["SPY"])["SPY"]
print("quote SPY", q.last, "delayed", q.delayed, q.timestamp)
price = (q.last * Decimal("0.5")).quantize(Decimal("0.01"), ROUND_FLOOR)
cid = make_client_order_id(date.today(), "paper_check", "SPY", Side.BUY, int(time.time()) % 100000)
oid = b.place_order(OrderRequest(cid, "SPY", Side.BUY, 1, limit_price=price))
time.sleep(2)
st = b.get_order(oid)
print("order", oid, st.state, st.limit_price, "client_order_id echoed:", st.client_order_id == cid)
print("in open orders:", oid in [o.order_id for o in b.get_open_orders()])
b.cancel_order(oid)
for _ in range(10):
    time.sleep(1)
    st = b.get_order(oid)
    if st.state.is_terminal:
        break
print("after cancel", st.state)
if st.state.name != "CANCELLED":
    raise SystemExit(f"!! 订单 {oid} 未撤销，请在老虎 APP 中检查模拟账户")
print("open orders left:", len(b.get_open_orders()))
