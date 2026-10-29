"""回测页：提交回测（面板唯一的写操作）、历史列表、净值与回撤曲线、指标对比。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pydantic import ValidationError

from quant.core.calendar import get_calendar
from quant.core.settings import CostSettings
from quant.dashboard import queries
from quant.engine.runner import CASH_KEY, COSTS_KEY
from quant.strategies import STRATEGIES
from quant.strategies.etf_momentum import MomentumParams

METRIC_LABELS = {
    "total_return": ("总收益", "pct"),
    "cagr": ("年化收益", "pct"),
    "volatility": ("年化波动", "pct"),
    "max_drawdown": ("最大回撤", "pct"),
    "sharpe": ("Sharpe", "num"),
    "calmar": ("Calmar", "num"),
    "turnover": ("年化换手", "num"),
    "trades": ("交易次数", "int"),
}


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value:.2%}"
    if kind == "num":
        return f"{value:.2f}"
    return str(value)


def build_submission(
    form: dict[str, Any], costs_configured: CostSettings
) -> tuple[dict[str, Any], list[str]]:
    """把表单值整理成 backtest_runs.params；返回 (params, 错误列表)。"""
    errors: list[str] = []
    params: dict[str, Any] = {
        "universe": list(form["universe"]),
        "safe_asset": form["safe_asset"],
        "lookback": int(form["lookback"]),
        "top_k": int(form["top_k"]),
        "sma_window": int(form["sma_window"]),
    }
    try:
        MomentumParams.from_mapping(params)
    except (ValueError, TypeError) as e:
        errors.append(f"策略参数：{e}")
    if form["start"] >= form["end"]:
        errors.append("开始日期必须早于结束日期")
    try:
        costs = CostSettings(
            commission_per_share=form["commission_per_share"],
            min_commission=form["min_commission"],
            slippage=form["slippage"],
        )
    except ValidationError as e:
        errors.append(f"成本参数：{e.errors()[0]['msg']}")
        costs = costs_configured
    if costs.commission_per_share is None or costs.min_commission is None:
        errors.append("请填写佣金（老虎实际费率待确认，见 issue #5）")
    params[COSTS_KEY] = {
        "commission_per_share": str(costs.commission_per_share),
        "min_commission": str(costs.min_commission),
        "slippage": str(costs.slippage),
    }
    params[CASH_KEY] = str(form["cash"])
    return params, errors


def _submit_form() -> None:
    settings = queries.settings()
    cfg = settings.costs
    defaults = MomentumParams()
    cal = get_calendar()
    last = cal.last_completed_trading_day(datetime.now(UTC))
    with st.form("submit_backtest"):
        st.subheader("提交回测")
        c1, c2, c3 = st.columns(3)
        strategy = c1.selectbox("策略", sorted(STRATEGIES))
        start = c2.date_input("开始", value=date(last.year - 10, last.month, 1), max_value=last)
        end = c3.date_input("结束", value=last, max_value=last)
        c1, c2, c3, c4 = st.columns(4)
        universe = c1.multiselect(
            "标的池",
            ["SPY", "QQQ", "TLT", "GLD", "IWM", "EFA", "EEM", "VNQ", "DBC"],
            default=list(defaults.universe),
        )
        safe_asset = c2.text_input("避险资产", value=defaults.safe_asset).strip().upper()
        lookback = c3.number_input("动量回看（交易日）", 5, 504, defaults.lookback)
        top_k = c4.number_input("持有数量 top_k", 1, 10, defaults.top_k)
        c1, c2, c3, c4, c5 = st.columns(5)
        sma_window = c1.number_input("均线窗口", 5, 400, defaults.sma_window)
        cash = c2.number_input("初始资金（美元）", 1000, 10_000_000, 100_000, step=1000)
        commission = c3.text_input(
            "每股佣金",
            value="" if cfg.commission_per_share is None else str(cfg.commission_per_share),
            placeholder="见 configs/base.yaml",
        )
        min_commission = c4.text_input(
            "单笔最低佣金",
            value="" if cfg.min_commission is None else str(cfg.min_commission),
            placeholder="见 configs/base.yaml",
        )
        slippage = c5.text_input("滑点", value=str(cfg.slippage))
        submitted = st.form_submit_button("提交", type="primary")
    if not submitted:
        return
    form = {
        "universe": universe, "safe_asset": safe_asset, "lookback": lookback, "top_k": top_k,
        "sma_window": sma_window, "start": start, "end": end, "cash": Decimal(str(cash)),
        "commission_per_share": commission or None, "min_commission": min_commission or None,
        "slippage": slippage or None,
    }  # fmt: skip
    params, errors = build_submission(form, cfg)
    if errors:
        for e in errors:
            st.error(e)
        return
    run_id = queries.submit_backtest(strategy, params, start, end)
    st.success(f"已提交回测 #{run_id}，backtest-worker 执行完成后显示在下方列表中")


def _runs_table(runs: pd.DataFrame) -> None:
    st.subheader("历史回测")
    if st.button("刷新"):
        queries.backtest_runs.clear()
        st.rerun()
    table = runs[["id", "策略", "状态", "开始", "结束", "参数", "提交时间", "错误"]].copy()
    for key in ("cagr", "max_drawdown", "sharpe"):
        label, kind = METRIC_LABELS[key]
        table[label] = runs["metrics"].map(lambda m, k=key, t=kind: _fmt(m.get(k), t))
    table["参数"] = table["参数"].map(lambda p: ", ".join(f"{k}={v}" for k, v in p.items()))
    st.dataframe(table, hide_index=True, width="stretch")


def _comparison(runs: pd.DataFrame) -> None:
    done = runs[runs["状态"] == "done"]
    if done.empty:
        return
    st.subheader("对比")
    labels = {
        int(i): f"#{i} {name} {a}~{b}"
        for i, name, a, b in zip(
            done["id"].tolist(), done["策略"], done["开始"], done["结束"], strict=True
        )
    }
    chosen = st.multiselect(
        "选择回测", list(labels), default=list(labels)[:2], format_func=labels.__getitem__
    )
    if not chosen:
        return
    eq = queries.backtest_equity(tuple(chosen))
    fig_eq, fig_dd = go.Figure(), go.Figure()
    for rid in chosen:
        part = eq[eq["run_id"] == rid]
        if part.empty:
            continue
        fig_eq.add_trace(
            go.Scatter(x=part["date"], y=part["equity"] / part["equity"].iloc[0], name=f"#{rid}")
        )
        fig_dd.add_trace(
            go.Scatter(x=part["date"], y=part["drawdown"], name=f"#{rid}", fill="tozeroy")
        )
    fig_eq.update_layout(title="净值（起点 = 1）", height=420, margin=dict(t=40, b=20))
    fig_dd.update_layout(title="回撤", height=280, yaxis_tickformat=".0%", margin=dict(t=40, b=20))
    st.plotly_chart(fig_eq, width="stretch")
    st.plotly_chart(fig_dd, width="stretch")

    rows = []
    for rid in chosen:
        m = done.loc[done["id"] == rid, "metrics"].iloc[0]
        bench = m.get("benchmark") or {}
        row = {"回测": labels[rid]}
        row |= {label: _fmt(m.get(k), kind) for k, (label, kind) in METRIC_LABELS.items()}
        row["基准年化"] = _fmt(bench.get("cagr"), "pct")
        row["基准回撤"] = _fmt(bench.get("max_drawdown"), "pct")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render() -> None:
    st.header("回测")
    if queries.engine() is None:
        st.warning("未配置数据库（QUANT_DATABASE_URL），无法提交或查看回测")
        return
    _submit_form()
    runs = queries.backtest_runs()
    if runs.empty:
        st.info("还没有回测记录")
        return
    _runs_table(runs)
    _comparison(runs)
