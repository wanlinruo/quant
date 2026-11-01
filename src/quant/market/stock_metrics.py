"""个股页展示的财务指标定义（stock_data 任务取数、个股页接口展示共用）。"""

from __future__ import annotations

# 个股页“估值与财务”展示的指标：（Finnhub 键, 标签, 类型）；pct 类 Finnhub 给的是百分数
METRICS: tuple[tuple[str, str, str], ...] = (
    ("peTTM", "市盈率（TTM）", "ratio"),
    ("forwardPE", "远期市盈率", "ratio"),
    ("pbQuarterly", "市净率", "ratio"),
    ("psTTM", "市销率（TTM）", "ratio"),
    ("evEbitdaTTM", "EV / EBITDA", "ratio"),
    ("currentDividendYieldTTM", "股息率", "pct"),
    ("roeTTM", "ROE（TTM）", "pct"),
    ("grossMarginTTM", "毛利率", "pct"),
    ("operatingMarginTTM", "营业利润率", "pct"),
    ("netProfitMarginTTM", "净利率", "pct"),
    ("revenueGrowthTTMYoy", "营收增长（TTM 同比）", "pct"),
    ("epsGrowthTTMYoy", "EPS 增长（TTM 同比）", "pct"),
    ("totalDebt/totalEquityQuarterly", "负债 / 股东权益", "ratio"),
)
METRIC_KEYS = tuple(k for k, _, _ in METRICS)
