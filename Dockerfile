# 单一镜像，不同启动命令区分角色（dashboard / scheduler / backtest-worker）

# 前端打包（只在构建阶段需要 Node，最终镜像只带 dist）
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    QUANT_CONFIG_DIR=/app/configs \
    QUANT_DATA_DIR=/data/parquet \
    QUANT_WEB_DIR=/app/web/dist

WORKDIR /app

# 先只装依赖，代码变动时可复用这一层缓存
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md ./
COPY src ./src
COPY configs ./configs
COPY alembic.ini ./
RUN uv sync --locked --no-dev
COPY --from=web /web/dist ./web/dist

RUN useradd --create-home --uid 1000 quant \
    && mkdir -p /data/parquet && chown -R quant:quant /data
USER quant

EXPOSE 8501
CMD ["uvicorn", "--factory", "quant.api.app:create_app", "--host", "0.0.0.0", "--port", "8501"]
