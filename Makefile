.PHONY: help install lint format test check up down logs sync-data

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

check: lint test ## 与 CI 相同的全部检查

COMPOSE_DEV = docker compose -f compose.yml -f compose.dev.yml

up: ## 本地启动 postgres + dashboard（http://localhost:8501，TRADING_MODE=sim）
	$(COMPOSE_DEV) up -d --build --wait

down: ## 停止本地服务（保留数据卷）
	$(COMPOSE_DEV) down

logs: ## 查看本地服务日志
	$(COMPOSE_DEV) logs -f --tail=100

sync-data: ## 从服务器同步 Parquet 数据（Phase 4 实现）
	@echo "尚未实现：服务器部署后提供（Phase 4）" >&2; exit 1
