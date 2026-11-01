#!/usr/bin/env bash
# 在服务器上执行（由 .github/workflows/deploy.yml 通过 SSH 调用，也可人工执行）：
#   bash /opt/quant/remote_deploy.sh ghcr.io/wanlinruo/quant:<sha>
# 拉取镜像 → 启动 postgres → Alembic 迁移 → 启动全部服务 → 安装每日备份 cron。
# scheduler 被重启时，执行中的任务会在重启后先恢复券商订单状态再继续。
set -euo pipefail
# 部署可能要等 scheduler 跑完当前任务（stop_grace_period 20 分钟）；SSH 断开也要把部署做完，
# 否则 scheduler 可能停下后不被重建（#79）
trap '' HUP
IMAGE=${1:?用法：remote_deploy.sh <镜像>}
cd /opt/quant
[[ -f .env ]] || { echo "缺少 /opt/quant/.env（见 deploy/server_init.md）" >&2; exit 1; }

if grep -q '^QUANT_IMAGE=' .env; then
  sed -i "s|^QUANT_IMAGE=.*|QUANT_IMAGE=${IMAGE}|" .env
else
  echo "QUANT_IMAGE=${IMAGE}" >> .env
fi

C=(docker compose -f compose.yml -f compose.server.yml)
"${C[@]}" pull --quiet
"${C[@]}" up -d --wait postgres
"${C[@]}" run --rm --no-deps dashboard alembic upgrade head
if [[ -n "$("${C[@]}" ps -q scheduler 2>/dev/null)" ]]; then
  echo "停止 scheduler：如有任务在跑会等它结束（最多 20 分钟）……"
  "${C[@]}" stop scheduler
  echo "scheduler 已停止"
fi
"${C[@]}" up -d --remove-orphans --wait
docker image prune -f >/dev/null

chmod +x /opt/quant/backup.sh
( crontab -l 2>/dev/null | grep -v '/opt/quant/backup.sh' || true
  echo '0 6 * * * /opt/quant/backup.sh >> /opt/quant/backups/backup.log 2>&1' ) | crontab -

"${C[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
