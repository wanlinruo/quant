#!/usr/bin/env bash
# 设置面板登录密码（#44）。在服务器上交互执行，密码不出现在命令行参数、日志或仓库中：
#   ssh -t ubuntu@<服务器> 'bash /opt/quant/set_dashboard_password.sh'
set -euo pipefail
cd /opt/quant
read -rsp "新密码（至少 12 位）：" p1; echo
read -rsp "再输一次：" p2; echo
[[ "$p1" == "$p2" ]] || { echo "两次输入不一致" >&2; exit 1; }
(( ${#p1} >= 12 )) || { echo "密码至少 12 位" >&2; exit 1; }
hash=$(printf '%s\n%s\n' "$p1" "$p1" | docker run --rm -i caddy:2 caddy hash-password)
unset p1 p2
[[ "$hash" == \$2a\$* ]] || { echo "生成密码哈希失败" >&2; exit 1; }
umask 077
grep -v '^DASHBOARD_PASSWORD_HASH=' .env > .env.tmp || true
# 单引号：compose 读取 .env 时不对 $ 做变量替换
echo "DASHBOARD_PASSWORD_HASH='${hash}'" >> .env.tmp
mv .env.tmp .env
docker compose -f compose.yml -f compose.server.yml up -d caddy
echo "已更新面板密码"
