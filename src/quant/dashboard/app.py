"""Streamlit 面板入口（PROJECT_PLAN 第 10 章）。

只读：不得包含任何下单、撤单、修改熔断开关的功能；唯一写操作是提交回测任务。
启动：streamlit run src/quant/dashboard/app.py
"""

from __future__ import annotations

import streamlit as st

from quant import __version__
from quant.core.settings import ConfigError
from quant.dashboard import queries
from quant.dashboard.pages import backtest, market, system, trading


def main() -> None:
    st.set_page_config(page_title="quant", layout="wide")
    try:
        settings = queries.settings()
    except (ConfigError, ValueError) as e:
        st.error(f"配置加载失败：{e}")
        return
    with st.sidebar:
        st.caption(f"quant {__version__}")
        st.caption(f"交易模式：**{settings.trading_mode}** · Broker：{settings.broker}")
        if settings.database_url is None:
            st.warning("未配置数据库：回测、交易、系统页不可用")
    nav = st.navigation(
        [
            st.Page(
                market.render,
                title="行情",
                icon=":material/candlestick_chart:",
                url_path="market",
                default=True,
            ),
            st.Page(backtest.render, title="回测", icon=":material/science:", url_path="backtest"),
            st.Page(
                trading.render, title="交易", icon=":material/account_balance:", url_path="trading"
            ),
            st.Page(
                system.render, title="系统", icon=":material/monitor_heart:", url_path="system"
            ),
        ]
    )
    nav.run()


main()
