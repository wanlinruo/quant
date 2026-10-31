"""FastAPI 应用。启动：uvicorn --factory quant.api.app:create_app --port 8501

/api/* 是接口；其它 GET 路径返回前端打包产物（React 单页应用，前端路由回落到 index.html）。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from quant.api.routes import backtests, market, meta, portfolio, system


def _mount_web(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


def create_app(static_dir: Path | None = None) -> FastAPI:
    """static_dir 为空时读环境变量 QUANT_WEB_DIR；都没有则只提供接口。"""
    app = FastAPI(title="quant 面板", docs_url=None, redoc_url=None)
    for r in (meta.router, portfolio.router, backtests.router, system.router, market.router):
        app.include_router(r, prefix="/api")
    web = static_dir or (
        Path(os.environ["QUANT_WEB_DIR"]) if os.environ.get("QUANT_WEB_DIR") else None
    )
    if web is not None and (web / "index.html").is_file():
        _mount_web(app, web)
    return app
