# 服务器初始化（#27）

腾讯云轻量应用服务器，东京，2 核 8G、80G SSD，Ubuntu 24.04 LTS。登录用户 `ubuntu`（uid 1000，与镜像内 `quant` 用户一致，容器可读取其 600 权限的配置文件）。
公网地址不写入仓库，只存在 GitHub Secrets（`DEPLOY_HOST`）与本机 `~/.ssh/known_hosts`。

## 1. 添加开发机公钥（控制台网页终端，人工）

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '<开发机公钥>' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
```

腾讯云控制台防火墙只保留 22 端口。

## 2. 系统初始化（开发机执行，可重复执行）

```bash
scp deploy/server_init.sh ubuntu@<服务器>:/tmp/
ssh ubuntu@<服务器> 'sudo nohup bash /tmp/server_init.sh > /tmp/server_init.log 2>&1 &'
```

脚本会重载 sshd，直接 `ssh ... 'sudo bash -s' < server_init.sh` 时连接可能被断开，所以放到后台执行。
完成后若存在 `/var/run/reboot-required`，执行 `sudo systemctl reboot`。

内容：时区 UTC；系统更新与自动安全更新；SSH 只允许密钥、禁止 root（`/etc/ssh/sshd_config.d/01-quant.conf`，
须排在 cloud-init 的 `50-` 之前才生效）；ufw 只开放 SSH 与 80/443（面板经 Caddy）；Docker Engine + Compose；容器日志 5 × 20MB 轮转；
journald 500MB；部署目录 `/opt/quant`。

## 3. 密钥与环境变量（人工放置，不经过仓库与 CI）

老虎配置文件（模拟账户）：

```bash
ssh ubuntu@<服务器> 'mkdir -p ~/.config/quant/tiger && chmod 700 ~/.config/quant ~/.config/quant/tiger'
scp ~/.config/quant/tiger/tiger_openapi_config.properties ubuntu@<服务器>:~/.config/quant/tiger/
ssh ubuntu@<服务器> 'chmod 600 ~/.config/quant/tiger/tiger_openapi_config.properties'
```

`/opt/quant/.env`（权限 600）：

```
TRADING_MODE=paper
COMPOSE_PROFILES=scheduler
TIGER_PROPS_DIR=/home/ubuntu/.config/quant/tiger
POSTGRES_PASSWORD=<openssl rand -hex 24，在服务器上生成>
QUANT_NOTIFY_WEBHOOK_URL=<企业微信机器人地址>
DASHBOARD_DOMAIN=<面板域名>
DASHBOARD_USER=<面板登录用户名>
DASHBOARD_PASSWORD_HASH=<由 set_dashboard_password.sh 写入>
QUANT_IMAGE=<由部署脚本写入>
# 服务器回测数据（#66）；第三方数据源 key（#60–#64，目前只有 TIINGO 与 EDGAR 被容器使用）
TIINGO_API_KEY=<Tiingo 免费档 key>
EDGAR_USER_AGENT=<名称 邮箱，SEC 要求的联系方式>
MASSIVE_API_KEY=<Massive 免费档 key>
FRED_API_KEY=<FRED key>
FINNHUB_API_KEY=<Finnhub 免费档 key>
```

写入 key 时不要用 `echo ... >> .env`（会留在 shell 历史）：

```
cd /opt/quant && read -rsp "Tiingo key: " k && printf 'TIINGO_API_KEY=%s\n' "$k" >> .env; unset k
```

首页市场看板（#69）：scheduler 每个交易日 20:30 ET 运行 `market_data`（Massive 全市场日线 →
`/data/parquet/market/snapshot.json`）。首次部署后可手动跑一次，约 25 分钟（Massive 免费档每分钟 5 次）：

```
cd /opt/quant && docker compose exec -T scheduler quant job market_data --date <最近交易日>
```

首页宏观、新闻、财经日历（#69 第 2 批）：scheduler 每个交易日 21:00 ET 运行 `market_extras`
（FRED 宏观序列与经济数据实际值、Coinbase 比特币、Finnhub 新闻与财报日历、Forex Factory 本周经济日历 →
`/data/parquet/market/{macro,news,calendar}.json`）。需要 `.env` 中的 `FRED_API_KEY`、`FINNHUB_API_KEY`
（未配置时对应区块跳过）；约 1 分钟。手动运行：

```
cd /opt/quant && docker compose exec -T scheduler quant job market_extras --date <最近交易日>
```

个股页新闻与财务指标（#70 第 3 批）：scheduler 每个交易日 21:15 ET 运行 `stock_data`（Finnhub，
标普 500 成分 ∪ 持仓；新闻每天全部，财务指标每天最旧的 120 只 → `/data/parquet/market/stocks/*.json`），
约 12 分钟，使用同一个 `FINNHUB_API_KEY`。手动运行：

```
cd /opt/quant && docker compose exec -T scheduler quant job stock_data --date <最近交易日>
```

回测数据补齐进度（只读）：

```
cd /opt/quant && docker compose exec -T backtest-worker quant backtest-data status
```

## 4. 部署（CI）

`main` 的 CI 通过后，`.github/workflows/deploy.yml` 构建镜像推送 GHCR，把 `compose.yml`、`compose.server.yml`、
`deploy/remote_deploy.sh`、`deploy/backup.sh` 复制到 `/opt/quant/`，再执行 `remote_deploy.sh`：
拉取镜像 → 启动 postgres → Alembic 迁移 → 启动全部服务 → 安装备份 cron。
GHCR 只在部署时用 CI 的临时令牌登录，部署后立即登出，服务器不保存凭据。

GitHub Secrets：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`（部署专用私钥，公钥在服务器 `authorized_keys`
中注释为 `quant-deploy-ci`）、`DEPLOY_KNOWN_HOSTS`（`ssh-keyscan -t ed25519 <服务器>`）。

## 5. 备份

`/opt/quant/backup.sh` 每天 UTC 06:00 运行：`pg_dump` + Parquet 数据卷打包到 `/opt/quant/backups/`，保留 30 天。
异地备份（对象存储）待确认。恢复：

```bash
cd /opt/quant
docker compose exec -T postgres pg_restore -U quant -d quant --clean --if-exists < backups/pg-<时间>.dump
docker run --rm -v quant_parquet:/data -v /opt/quant/backups:/b alpine tar -C /data -xzf /b/parquet-<时间>.tar.gz
```

## 6. 访问面板

公网（#44）：`https://<DASHBOARD_DOMAIN>`，Caddy 自动申请 HTTPS 证书，需输入用户名和密码。
前提：子域名 A 记录指向服务器；腾讯云防火墙与 ufw 放行 80/443；`.env` 中设置 `DASHBOARD_DOMAIN`、`DASHBOARD_USER`。
设置或修改密码（交互输入，密码不经过聊天、仓库或命令行参数）：

```bash
ssh -t ubuntu@<服务器> 'bash /opt/quant/set_dashboard_password.sh'
```

未设置密码时使用占位哈希，任何密码都无法登录。

也可以用 SSH 隧道：

```bash
ssh -N -L 8501:127.0.0.1:8501 ubuntu@<服务器>
# 浏览器打开 http://localhost:8501
```

## 7. 首次启用模拟盘

```bash
ssh ubuntu@<服务器>
cd /opt/quant
docker compose run --rm scheduler quant baseline            # 券商当前持仓记为对账基准
docker compose run --rm scheduler quant trading enable --yes  # 人工打开熔断开关
```

## 已知限制

- 东京服务器的 IP 被 Yahoo Finance 限流（所有接口返回 429），服务器上不能用 yfinance。
