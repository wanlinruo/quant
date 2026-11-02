# 系统页 B 批（资源采集）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成设计 5.5 剩下的部分：4 个资源卡（24 小时走势）、服务容器、资源趋势 · 7 天、磁盘写满预测。

**Architecture:** 新服务 `monitor`（`python -m quant.jobs.monitor`）每 60 秒采集主机 CPU / 内存（容器内 `/proc` 即主机数据）、磁盘（数据卷所在文件系统）与各容器状态，写入新表 `system_metrics`，保留 8 天。容器状态经 `docker-socket-proxy` 读取：只开放 `CONTAINERS`（GET），POST 等写操作默认关闭；代理只在内部网络 `dockerproxy` 上，只有 monitor 能访问。面板仍只读数据库，不访问 Docker。调度器不变（串行任务会阻塞采集，所以单独成服务）。

**Spec:** 设计 5.5、7.2；#71；#63（用户已确认可访问 Docker）。

## Global Constraints

- 只读：代理不允许任何写操作；monitor 不保存、不打印容器环境变量（inspect 返回的 Env 含密钥）。
- 采集失败只记日志，不退出；收到 SIGTERM 时退出。
- 表结构经 Alembic 迁移。

## 决定

- `system_metrics`：`id`、`ts`（timestamptz）、`kind`（host / container）、`name`、`cpu_pct`、`mem_bytes`、`mem_limit`、`disk_used`、`disk_total`、`extra`（JSONB：state、status、started_at、restarts）。索引 `(kind, name, ts)`。每小时删 8 天前的数据。
- 主机 CPU：两次 `/proc/stat` 的差；内存：`MemTotal - MemAvailable`；磁盘：`shutil.disk_usage("/data/parquet")`。
- 容器：`GET /containers/json?all=1` 只取 compose 项目 `quant` 的；`/containers/{id}/stats?stream=false` 算 CPU%（相对前一次采样）与内存（usage − inactive_file）；`/containers/{id}/json` 取 `RestartCount`、`State.StartedAt`。
- 老虎接口延迟：monitor 不持有券商凭证，本批卡片显示“待接入”（Ruling）。
- 接口：概览增加 `resources`（CPU、内存、磁盘的当前值与 24 小时 5 分钟均值序列）、`containers`（最近一次采样；超过 5 分钟没采样标“失联”）、`trends`（7 天每小时均值）、`disk`（7 天线性拟合的每日增量与预计写满天数，跨度不足 24 小时不预测）、`version`。
- 横幅（注意）：曾有采样但超过 5 分钟没有新采样（monitor 停了）；磁盘 ≥ 85% 或预计 30 天内写满；容器不在运行。

## Review Focus

1. 从未采集过（本地、刚部署）：各面板显示“暂无采样”，横幅不告警。
2. 容器 stats 请求失败或 Docker 代理不可用：主机数据照常写入。
3. 磁盘用量下降或不变：不预测写满。
4. 第一次采样没有前一次 CPU 计数：CPU 记空，不记 0。
5. 容器 inspect 的 Env 不进入数据库与日志。

### Task 1: 表与采集（`db/models.py`、迁移、`jobs/monitor.py`、`tests/test_monitor.py`）
### Task 2: compose（docker-proxy、monitor）
### Task 3: 接口（`system_view.py`、`schemas.py`、`tests/test_api_system.py`）
### Task 4: 前端（资源卡、服务容器、趋势、磁盘预测）
### Task 5: `make check`、终审、部署、服务器核对，记入 #71
