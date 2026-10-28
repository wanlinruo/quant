"""回测页：表单校验（无数据库）与 提交 → 执行 → 展示（需要数据库）。"""

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
import streamlit as st
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from quant.core.settings import CostSettings
from quant.dashboard.pages.backtest import build_submission

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="未设置 QUANT_TEST_DATABASE_URL")

FORM: dict[str, Any] = {
    "universe": ["SPY", "QQQ"], "safe_asset": "SHY", "lookback": 126, "top_k": 1,
    "sma_window": 200, "start": date(2016, 1, 4), "end": date(2026, 10, 28),
    "cash": Decimal("100000"), "commission_per_share": "0.005", "min_commission": "1",
    "slippage": "0.0005",
}  # fmt: skip


def test_build_submission_ok() -> None:
    params, errors = build_submission(FORM, CostSettings())
    assert errors == []
    assert params["universe"] == ["SPY", "QQQ"]
    assert params["_costs"] == {
        "commission_per_share": "0.005", "min_commission": "1", "slippage": "0.0005",
    }  # fmt: skip
    assert params["_cash"] == "100000"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"commission_per_share": None}, "佣金"),
        ({"min_commission": "-1"}, "成本参数"),
        ({"top_k": 3}, "策略参数"),
        ({"safe_asset": "SPY"}, "策略参数"),
        ({"universe": []}, "策略参数"),
        ({"start": date(2026, 10, 28)}, "开始日期"),
    ],
)
def test_build_submission_errors(change: dict[str, Any], message: str) -> None:
    _, errors = build_submission({**FORM, **change}, CostSettings())
    assert any(message in e for e in errors), errors


def _page() -> None:
    from quant.dashboard.pages import backtest

    backtest.render()


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[str]:
    assert DB_URL
    monkeypatch.setenv("QUANT_DATABASE_URL", DB_URL)
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    st.cache_resource.clear()
    st.cache_data.clear()
    yield DB_URL
    st.cache_resource.clear()
    st.cache_data.clear()


def test_page_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUANT_DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    st.cache_resource.clear()
    at = AppTest.from_function(_page, default_timeout=30).run()
    assert not at.exception
    assert any("未配置数据库" in w.value for w in at.warning)


@pytest.mark.db
@needs_db
def test_submit_execute_display(db_env: str) -> None:
    from quant.db.backtests import complete_run
    from quant.db.models import BacktestRun
    from quant.engine.metrics import compute_metrics, drawdown

    at = AppTest.from_function(_page, default_timeout=30).run()
    assert not at.exception
    assert any("还没有回测记录" in i.value for i in at.info)

    # 未填佣金：拒绝提交
    at.button[0].click().run()
    assert any("佣金" in e.value for e in at.error)

    inputs = {t.label: t for t in at.text_input}
    inputs["每股佣金"].set_value("0.005")
    inputs["单笔最低佣金"].set_value("1")
    at.button[0].click().run()
    assert not at.exception
    assert any("已提交回测" in s.value for s in at.success)

    engine = create_engine(db_env)
    with Session(engine) as s:
        run = s.scalars(select(BacktestRun)).one()
        assert run.status == "queued"
        assert run.params["_costs"]["commission_per_share"] == "0.005"
        # 模拟 worker 执行完成
        eq = pd.Series([100.0, 105.0, 102.0], index=pd.bdate_range("2026-10-26", periods=3))
        complete_run(
            s, run.id, {**compute_metrics(eq).to_dict(), "benchmark": None}, eq, drawdown(eq)
        )
        s.commit()
    engine.dispose()

    st.cache_data.clear()
    at = AppTest.from_function(_page, default_timeout=30).run()
    assert not at.exception
    assert at.multiselect[-1].value == [1]  # 对比默认选中已完成的回测
    assert len(at.dataframe) == 2  # 历史列表 + 指标对比
