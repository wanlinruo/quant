"""应用外壳：托管前端打包产物、SPA 回落、OpenAPI 导出。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant.api.app import create_app

REPO = Path(__file__).resolve().parents[1]
WEB_OPENAPI = REPO / "web" / "src" / "api" / "openapi.json"


def test_spa_fallback_and_api_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("x")
    c = TestClient(create_app(static_dir=tmp_path))
    for p in ("/", "/backtest/128", "/stock/NVDA", "/portfolio?account=live"):
        r = c.get(p)
        assert r.status_code == 200 and 'id="root"' in r.text, p
    assert c.get("/assets/a.js").text == "x"
    assert c.get("/assets/missing.js").status_code == 404
    r = c.get("/api/不存在")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_no_static_dir_serves_api_only(tmp_path: Path) -> None:
    c = TestClient(create_app(static_dir=None))
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/backtest/1").status_code == 404


def _export() -> str:
    return subprocess.run(
        [sys.executable, "-m", "quant.api.openapi"], capture_output=True, text=True, check=True
    ).stdout


def test_openapi_export_has_submit() -> None:
    assert json.loads(_export())["paths"]["/api/backtests"]["post"]


@pytest.mark.skipif(not WEB_OPENAPI.exists(), reason="前端生成文件尚未提交（Task 4）")
def test_openapi_export_matches_frontend() -> None:
    assert WEB_OPENAPI.read_text(encoding="utf-8") == _export(), "运行 make api-types 重新生成"
