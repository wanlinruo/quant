"""FastAPI 应用。启动：uvicorn --factory quant.api.app:create_app --port 8501"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from quant.api.routes import backtests, market, meta, portfolio, system


def create_app(static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="quant 面板", docs_url=None, redoc_url=None)
    for r in (meta.router, portfolio.router, backtests.router, system.router, market.router):
        app.include_router(r, prefix="/api")
    return app
