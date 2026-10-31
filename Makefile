.PHONY: help install lint format test check up down logs migrate sync-data web-install web-dev web-check api-types

help: ## 列出可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## 安装依赖（含开发依赖）
	uv sync

lint: ## ruff 检查 + 格式检查 + mypy
	uv run ruff check
	uv run ruff format --check
	uv run mypy

format: ## 自动修复 lint 并格式化
	uv run ruff check --fix
	uv run ruff format

test: ## 运行单元测试（TRADING_MODE=sim）
	TRADING_MODE=sim uv run pytest

check: lint test web-check ## 与 CI 相同的全部检查

web-install: ## 安装前端依赖
	cd web && npm ci

web-dev: ## 前端开发服务器（http://localhost:5173，/api 代理到 127.0.0.1:8501）
	cd web && npm run dev

web-check: ## 前端 lint + 类型检查 + 测试 + 构建
	cd web && npm run lint && npm run typecheck && npm run test && npm run build

api-types: ## 由后端 OpenAPI 重新生成前端接口类型
	uv run python -m quant.api.openapi > web/src/api/openapi.json
	cd web && npm run gen:types

COMPOSE_DEV = docker compose -f compose.yml -f compose.dev.yml

up: ## 本地启动 postgres + 面板（http://localhost:8501，TRADING_MODE=sim）
	$(COMPOSE_DEV) up -d --build --wait

down: ## 停止本地服务（保留数据卷）
	$(COMPOSE_DEV) down

migrate: ## 在本地 postgres 上执行数据库迁移（alembic upgrade head）
	$(COMPOSE_DEV) run --rm dashboard alembic upgrade head

logs: ## 查看本地服务日志
	$(COMPOSE_DEV) logs -f --tail=100

sync-data: ## 从服务器同步 Parquet 数据（Phase 4 实现）
	@echo "尚未实现：服务器部署后提供（Phase 4）" >&2; exit 1
