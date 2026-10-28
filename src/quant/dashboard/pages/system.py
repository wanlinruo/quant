"""系统页（只读）：任务运行状态、最近失败、交易模式与熔断开关、回测队列。

熔断开关只显示，不提供修改入口（PROJECT_PLAN 第 10 章）。
"""

from __future__ import annotations

import streamlit as st

from quant.dashboard import queries


def render() -> None:
    st.header("系统")
    settings = queries.settings()
    flags = queries.flags() if queries.engine() is not None else {}
    enabled = flags.get("trading_enabled")
    c1, c2, c3 = st.columns(3)
    c1.metric("交易模式", settings.trading_mode)
    c2.metric("Broker", settings.broker)
    c3.metric(
        "熔断开关 trading_enabled",
        {"true": "开启（允许下单）", "false": "关闭（禁止下单）"}.get(str(enabled), "未知"),
    )
    st.caption("熔断开关只能由人工在数据库中修改，面板只读。")
    if queries.engine() is None:
        st.warning("未配置数据库（QUANT_DATABASE_URL）")
        return

    st.subheader("定时任务")
    jobs = queries.job_status()
    if jobs.empty:
        st.info("还没有任务运行记录（Phase 4 部署调度后产生）")
    else:
        st.dataframe(jobs, hide_index=True, width="stretch")

    st.subheader("最近失败")
    failures = queries.recent_failures()
    if failures.empty:
        st.success("没有失败记录")
    else:
        st.dataframe(failures, hide_index=True, width="stretch")

    st.subheader("回测队列")
    q = queries.queue_status()
    cols = st.columns(4)
    for col, status in zip(cols, ["queued", "running", "done", "failed"], strict=True):
        col.metric(status, q.get(status, 0))
    if q.get("running"):
        st.caption("running 长时间不变时，可能是 worker 被强制终止，需要人工把状态改回 queued。")
