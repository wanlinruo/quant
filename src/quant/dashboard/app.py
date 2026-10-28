"""Streamlit 面板入口（PROJECT_PLAN 第 10 章）。

只读：不得包含任何下单、撤单、修改熔断开关的功能；唯一写操作是提交回测任务（后续实现）。
启动：streamlit run src/quant/dashboard/app.py
"""

from __future__ import annotations

import streamlit as st

from quant import __version__
from quant.core.settings import ConfigError, load_settings


def main() -> None:
    st.set_page_config(page_title="quant", layout="wide")
    st.title("quant 面板")
    try:
        settings = load_settings()
    except (ConfigError, ValueError) as e:
        st.error(f"配置加载失败：{e}")
        return
    col1, col2, col3 = st.columns(3)
    col1.metric("交易模式", settings.trading_mode)
    col2.metric("Broker", settings.broker)
    col3.metric("版本", __version__)
    st.info("行情、回测、交易、系统页面将在 Phase 3 实现。")


main()
