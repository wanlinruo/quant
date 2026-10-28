"""面板测试：AppTest 无头运行，不连数据库；以及“只读”约束的静态检查。"""

import ast
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from quant.core.calendar import get_calendar
from quant.data.store import ParquetStore

DASHBOARD = Path(__file__).resolve().parents[1] / "src" / "quant" / "dashboard"
APP = str(DASHBOARD / "app.py")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    for k in ("QUANT_DATABASE_URL", "TIGER_PROPS_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRADING_MODE", "sim")
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    st.cache_resource.clear()
    st.cache_data.clear()
    yield tmp_path
    st.cache_resource.clear()
    st.cache_data.clear()


def _write_sample(root: Path) -> None:
    cal = get_calendar()
    end = cal.last_completed_trading_day(datetime.now(UTC))
    days = pd.DatetimeIndex(cal.trading_days(end.replace(year=end.year - 2), end), name="date")
    close = 100 + np.arange(len(days)) * 0.1
    df = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "adj_close": close,
         "volume": np.full(len(days), 1000, dtype="int64")},
        index=days,
    )  # fmt: skip
    ParquetStore(root).write_bars("SPY", df, "test")


def test_app_without_data(env: Path) -> None:
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "行情" in [h.value for h in at.header]
    assert any("还没有行情数据" in i.value for i in at.info)
    assert any("未配置数据库" in w.value for w in at.sidebar.warning)


def test_market_page_with_data(env: Path) -> None:
    _write_sample(env)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("校验通过" in s.value for s in at.success)
    assert at.selectbox[0].options == ["SPY"]
    at.toggle[0].set_value(True).run()
    assert not at.exception


def test_invalid_mode_shows_error(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "bogus")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("配置加载失败" in e.value for e in at.error)


# ---- 只读约束：面板代码不得具备下单、撤单、修改熔断开关的能力 ----

FORBIDDEN_IMPORTS = ("quant.broker", "tigeropen", "quant.core.safety", "quant.engine.executor")
# 只读展示允许查询 orders / fills / system_flags，但不得出现任何写调用（见 WRITE_CALLS）
FORBIDDEN_NAMES = {"place_order", "cancel_order", "plan_orders", "check_order_allowed"}
SESSION_NAMES = {"s", "session", "conn", "connection"}
ALLOWED_WRITES = {"create_run"}  # 只能提交回测任务
WRITE_CALLS = {
    "add",
    "add_all",
    "delete",
    "merge",
    "execute",
    "update",
    "insert",
    "commit",
    "flush",
}


def _sources() -> list[Path]:
    return sorted(DASHBOARD.rglob("*.py"))


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_dashboard_is_read_only(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(FORBIDDEN_IMPORTS), (
                f"{path.name} 导入了 {node.module}"
            )
            names = {a.name for a in node.names}
            assert not names & FORBIDDEN_NAMES, f"{path.name} 导入了 {names & FORBIDDEN_NAMES}"
            if node.module == "quant.db.backtests":
                assert names <= ALLOWED_WRITES | {"make_engine"}, names
        if isinstance(node, ast.Import):
            for a in node.names:
                assert not a.name.startswith(FORBIDDEN_IMPORTS), f"{path.name} 导入了 {a.name}"
        if isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            assert name not in FORBIDDEN_NAMES, f"{path.name} 使用了 {name}"
        # 数据库会话 / 连接上的写调用（面板代码中会话变量统一命名为 s / session / conn）
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in SESSION_NAMES
        ):
            assert node.func.attr not in WRITE_CALLS, f"{path.name} 调用了 .{node.func.attr}()"
