#!/usr/bin/env bash
# 每日备份（PROJECT_PLAN 13.2 第 7 条，#27）：pg_dump + Parquet 打包，本机保留 30 天。
# 由 cron 在 UTC 06:00（美东凌晨，无任务运行）调用：
#   0 6 * * * /opt/quant/backup.sh >> /opt/quant/backups/backup.log 2>&1
# 异地备份（对象存储）待确认后再加。
set -euo pipefail

DIR=/opt/quant
OUT="$DIR/backups"
KEEP_DAYS=30
STAMP=$(date -u +%Y%m%dT%H%MZ)
cd "$DIR"
umask 077

docker compose exec -T postgres pg_dump -U quant -d quant --format=custom > "$OUT/pg-$STAMP.dump"
# Parquet 数据卷：用一次性容器只读挂载后打包
docker run --rm -v quant_parquet:/data:ro alpine tar -C /data -czf - . > "$OUT/parquet-$STAMP.tar.gz"

find "$OUT" -name 'pg-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$OUT" -name 'parquet-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) 备份完成：pg-$STAMP.dump $(du -h "$OUT/pg-$STAMP.dump" | cut -f1)，" \
  "parquet-$STAMP.tar.gz $(du -h "$OUT/parquet-$STAMP.tar.gz" | cut -f1)"
