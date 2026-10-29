#!/usr/bin/env bash
# 服务器初始化（PROJECT_PLAN 13.2，#27）。可重复执行。
#
# 用法（在 Mac 上，已能用密钥登录 ubuntu 用户）：
#   ssh ubuntu@<服务器> 'sudo bash -s' < deploy/server_init.sh
#
# 内容：时区 UTC、系统更新与自动安全更新、SSH 只允许密钥且禁止 root、ufw 只开放 SSH、
# Docker Engine + Compose 插件、容器日志轮转、journald 限额、部署目录 /opt/quant。
# 不包含任何密钥；老虎配置文件与 .env 由用户自行放置（见 deploy/server_init.md）。
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_DIR=/opt/quant

[[ $EUID -eq 0 ]] || { echo "请用 sudo 运行" >&2; exit 1; }
id "$DEPLOY_USER" >/dev/null
# 关闭密码登录前，部署用户必须已有公钥
[[ -s "/home/$DEPLOY_USER/.ssh/authorized_keys" ]] || {
  echo "/home/$DEPLOY_USER/.ssh/authorized_keys 为空，先添加公钥" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "== 时区 UTC（业务时区由应用处理）"
timedatectl set-timezone UTC

echo "== 系统更新与自动安全更新"
apt-get update -q
apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -yq
apt-get install -yq ca-certificates curl gnupg ufw unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== SSH：只允许密钥登录，禁止 root"
# sshd 对同一选项取第一次出现的值；文件按名称排序，01- 排在 cloud-init 的 50- 之前
cat > /etc/ssh/sshd_config.d/01-quant.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sshd -t
systemctl reload ssh

echo "== 防火墙：只开放 SSH"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
# 面板公网入口（Caddy，HTTPS + 登录密码，#44）。注意 Docker 发布的端口不经过 ufw，
# 对外开放的只有 compose.server.yml 中 caddy 的 80/443；dashboard 只绑定 127.0.0.1
ufw allow 80/tcp
ufw allow 443
ufw --force enable

echo "== Docker Engine + Compose（官方 apt 源）"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
fi
# 容器日志轮转：每个容器最多 5 × 20MB
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
EOF
systemctl enable --now docker
systemctl restart docker
usermod -aG docker "$DEPLOY_USER"

echo "== journald 限额"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/quant.conf <<'EOF'
[Journal]
SystemMaxUse=500M
EOF
systemctl restart systemd-journald

echo "== 部署目录"
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_DIR" "$DEPLOY_DIR/backups"

echo "== 完成"
timedatectl show -p Timezone
ufw status | head -5
docker --version
docker compose version
