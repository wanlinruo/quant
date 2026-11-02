"""只读约束：面板接口不得具备下单、撤单、修改熔断开关的能力；唯一写操作是提交回测。"""

import ast
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from quant.api.app import API_PREFIX, ROUTERS, create_app

API_DIR = Path(__file__).resolve().parents[1] / "src" / "quant" / "api"

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FORBIDDEN_IMPORTS = ("quant.broker", "tigeropen", "quant.core.safety", "quant.engine.executor")
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


def test_only_backtest_submission_writes() -> None:
    paths = create_app().openapi()["paths"]
    writes = {(m.upper(), p) for p, ops in paths.items() for m in ops if m.upper() in WRITE_METHODS}
    assert writes == {("POST", "/api/backtests")}


def test_only_one_write_decorator_in_source() -> None:
    """源码层面再查一次：路由装饰器中只有一处 .post，没有 put / patch / delete。"""
    found = []
    for path in sorted(API_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef):
                for d in node.decorator_list:
                    if (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr.upper() in WRITE_METHODS | {"API_ROUTE", "ADD_API_ROUTE"}
                    ):
                        found.append((path.name, d.func.attr))
    assert found == [("backtests.py", "post")]


@pytest.mark.parametrize("path", sorted(API_DIR.rglob("*.py")), ids=lambda p: p.name)
def test_api_is_read_only(path: Path) -> None:
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
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in SESSION_NAMES
        ):
            assert node.func.attr not in WRITE_CALLS, f"{path.name} 调用了 .{node.func.attr}()"


def _get_paths(tmp: Path) -> list[str]:
    """所有 GET 路由（含 include_in_schema=False 与非装饰器注册的），路径参数填 1。"""
    routes = [(API_PREFIX, r) for router in ROUTERS for r in router.routes]
    routes += [("", r) for r in create_app(static_dir=tmp).routes]
    out = {
        prefix + r.path.replace("{path:path}", "x")
        for prefix, r in routes
        if isinstance(r, APIRoute) and "GET" in (r.methods or set())
    }
    return sorted(re.sub(r"\{[^}]+\}", "1", p) for p in out)


def test_write_methods_rejected(tmp_path: Path) -> None:
    """行为检查：除提交回测外，任何 GET 路径的写方法都返回 405（覆盖非装饰器注册的路由）。"""
    (tmp_path / "index.html").write_text("<html></html>")
    paths = _get_paths(tmp_path)
    assert "/api/health" in paths and "/x" in paths and len(paths) > 30
    client = TestClient(create_app(static_dir=tmp_path))
    for path in paths:
        for method in sorted(WRITE_METHODS):
            if (method, path) == ("POST", "/api/backtests"):
                continue
            assert client.request(method, path).status_code == 405, (method, path)
