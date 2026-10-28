"""交易页（只读）：持仓、目标与实际差异、订单与成交、账户净值曲线。"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from quant.dashboard import queries


def _target_vs_actual(pos: pd.DataFrame, signal: pd.DataFrame) -> pd.DataFrame:
    total = pos["市值"].sum() if len(pos) else 0.0
    actual = (
        {str(s): float(v) / total for s, v in zip(pos["标的"], pos["市值"], strict=True)}
        if total
        else {}
    )
    target: dict[str, float] = signal.iloc[0]["目标权重"] if len(signal) else {}
    syms = sorted(set(actual) | set(target))
    return pd.DataFrame(
        {
            "标的": syms,
            "目标权重": [target.get(s, 0.0) for s in syms],
            "实际权重": [actual.get(s, 0.0) for s in syms],
        }
    ).assign(差异=lambda d: d["实际权重"] - d["目标权重"])


def render() -> None:
    st.header("交易")
    if queries.engine() is None:
        st.warning("未配置数据库（QUANT_DATABASE_URL）")
        return
    modes = ["paper", "live", "sim"]
    current = queries.settings().trading_mode
    mode = st.segmented_control("账户", modes, default=current if current in modes else "paper")
    mode = mode or current

    curve = queries.account_curve(mode)
    pos = queries.positions(mode)
    signal = queries.latest_signal()
    if curve.empty and pos.empty and signal.empty:
        st.info(f"{mode} 账户还没有交易记录（Phase 4 部署每日任务后产生）")
        return

    if len(curve):
        last = curve.iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("净值", f"${last['净值']:,.2f}")
        c2.metric("现金", f"${last['现金']:,.2f}")
        c3.metric("快照日", str(last["日期"]))
        fig = go.Figure(go.Scatter(x=curve["日期"], y=curve["净值"], name="净值"))
        fig.update_layout(title="账户净值", height=320, margin=dict(t=40, b=20))
        st.plotly_chart(fig, width="stretch")

    st.subheader("持仓与目标")
    if len(signal):
        sig = signal.iloc[0]
        st.caption(f"目标来自 {sig['策略']} {sig['信号日']} 的信号（数据截至 {sig['数据截至']}）")
    diff = _target_vs_actual(pos, signal)
    st.dataframe(
        diff.style.format({"目标权重": "{:.1%}", "实际权重": "{:.1%}", "差异": "{:+.1%}"}),
        hide_index=True,
        width="stretch",
    )
    if len(pos):
        st.dataframe(pos, hide_index=True, width="stretch")

    st.subheader("订单")
    st.dataframe(queries.orders(mode), hide_index=True, width="stretch")
    st.subheader("成交")
    st.dataframe(queries.fills(mode), hide_index=True, width="stretch")
