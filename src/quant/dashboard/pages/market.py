"""行情页：数据状态（最新日期、校验结果）与 K 线。"""

from __future__ import annotations

from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from quant.dashboard import queries


def render() -> None:
    st.header("行情")
    status = queries.data_status()
    if status.empty:
        st.info("本地还没有行情数据。运行一次回测（命令行或回测页）会自动拉取并校验数据。")
        return

    failing = status[status["校验"] != "通过"]
    if failing.empty:
        st.success(f"{len(status)} 个标的最近一年数据校验通过")
    else:
        st.error(f"{len(failing)} 个标的校验未通过：{', '.join(failing['标的'])}")
    st.dataframe(status, hide_index=True, width="stretch")

    col1, col2, col3 = st.columns([1, 1, 1])
    symbol = col1.selectbox("标的", status["标的"].tolist())
    years = col2.select_slider(
        "区间", options=[1, 2, 3, 5, 10, 20], value=2, format_func=lambda y: f"{y} 年"
    )
    adjusted = col3.toggle("含分红复权", value=False, help="关闭：仅拆股调整（与券商行情一致）")
    df = queries.bars(symbol, date.today() - timedelta(days=365 * years))
    if df.empty:
        st.warning("区间内没有数据")
        return
    if adjusted:
        factor = df["adj_close"] / df["close"]
        o, h, lo, c = (df[k] * factor for k in ("open", "high", "low", "close"))
    else:
        o, h, lo, c = df["open"], df["high"], df["low"], df["close"]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03
    )
    fig.add_trace(
        go.Candlestick(x=df.index, open=o, high=h, low=lo, close=c, name=symbol), row=1, col=1
    )
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="成交量", marker_color="#9aa5b1"), row=2, col=1
    )
    fig.update_layout(
        height=560, xaxis_rangeslider_visible=False, showlegend=False, margin=dict(t=20, b=20)
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig, width="stretch")
