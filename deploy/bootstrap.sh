#!/usr/bin/env bash
# ===========================================
# 阿里云轻量应用服务器 / ECS（Ubuntu 22.04）一键开机部署
#
# 用法：
#   ssh root@<server-ip>
#   curl -fsSL https://raw.githubusercontent.com/<your-org>/tt-shop/main/deploy/bootstrap.sh | bash
#   # 或先 clone 再跑
#   git clone https://github.com/<your-org>/tt-shop.git /opt/tt-shop
#   cd /opt/tt-shop && bash deploy/bootstrap.sh
#
# 这个脚本只跑"基础环境"——装 docker / 建目录 / 配防火墙 / 装 systemd unit。
# 真正起服务前还要：
#   1. 把 .env.production 上传到 /opt/tt-shop/（chmod 600）
#   2. （可选）拉好镜像 / 等数据卷迁移完
#   3. systemctl start tt-shop
# ===========================================

set -euo pipefail

# 颜色
red() { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        red "必须以 root 运行：sudo bash $0"
        exit 1
    fi
}

require_ubuntu_22() {
    if ! grep -q 'Ubuntu 22' /etc/os-release; then
        yellow "警告：非 Ubuntu 22.04，可能需要手动调整 apt 包名"
        read -p "继续？(y/N) " -n 1 -r
        echo
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
    fi
}

step() { echo; green "==> $*"; }

# -------------------------------------------
# 1. 系统包基础
# -------------------------------------------
install_base_packages() {
    step "1/7 升级 apt 索引 + 装基础包"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg lsb-release \
        ufw fail2ban tzdata git unzip \
        python3 python3-pip
    timedatectl set-timezone UTC
}

# -------------------------------------------
# 2. Docker + Docker Compose Plugin
# -------------------------------------------
install_docker() {
    step "2/7 装 Docker（官方源）"
    if command -v docker >/dev/null 2>&1; then
        green "Docker 已存在，跳过"
        return
    fi
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    green "docker 版本：$(docker --version)"
    green "compose 版本：$(docker compose version)"
}

# -------------------------------------------
# 3. 防火墙：只开 22/80/443
# -------------------------------------------
setup_firewall() {
    step "3/7 配置 UFW（22 / 80 / 443）"
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp comment 'SSH'
    ufw allow 80/tcp comment 'HTTP -> Caddy ACME'
    ufw allow 443/tcp comment 'HTTPS -> Caddy'
    ufw allow 443/udp comment 'HTTP/3 QUIC'
    yes | ufw enable || true
    ufw status verbose
}

# -------------------------------------------
# 4. fail2ban：防 SSH 暴力破解
# -------------------------------------------
setup_fail2ban() {
    step "4/7 启用 fail2ban（保护 SSH）"
    cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
EOF
    systemctl enable fail2ban
    systemctl restart fail2ban
}

# -------------------------------------------
# 5. 数据目录
# -------------------------------------------
setup_data_dirs() {
    step "5/7 建 /data 目录（Postgres / Redis / Caddy 持久化卷）"
    mkdir -p /data/pg /data/redis /data/caddy/data /data/caddy/config /data/backups
    # Postgres 容器内 UID 70（alpine），Redis 是 999，Caddy 是 root；不限定 owner，让容器自管
    chmod 700 /data/pg /data/redis
    df -h /data | tail -1
}

# -------------------------------------------
# 6. 项目代码
# -------------------------------------------
setup_app_dir() {
    step "6/7 准备 /opt/tt-shop 项目目录"
    mkdir -p /opt/tt-shop
    if [[ ! -d /opt/tt-shop/.git ]]; then
        yellow "  /opt/tt-shop 还没有 git 仓库，请手动："
        yellow "    git clone <repo-url> /opt/tt-shop"
        yellow "  或把项目 tarball 解压到这里"
    else
        green "  /opt/tt-shop 已存在 git 仓库，跳过 clone"
    fi
    if [[ ! -f /opt/tt-shop/.env.production ]]; then
        yellow "  /opt/tt-shop/.env.production 不存在，请从本地 scp 上传："
        yellow "    scp deploy/env.production.example root@<server>:/opt/tt-shop/.env.production"
        yellow "    然后 ssh 上去填好真实凭证 + chmod 600 .env.production"
    fi
}

# -------------------------------------------
# 7. systemd unit（兜底自启）
# -------------------------------------------
install_systemd_unit() {
    step "7/7 装 systemd unit /etc/systemd/system/tt-shop.service"
    if [[ -f /opt/tt-shop/deploy/systemd/tt-shop.service ]]; then
        cp /opt/tt-shop/deploy/systemd/tt-shop.service /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable tt-shop.service
        green "已 enable，但暂不 start（等 .env.production 准备好再 systemctl start tt-shop）"
    else
        yellow "  没找到 deploy/systemd/tt-shop.service，跳过"
    fi
}

# -------------------------------------------
# 主流程
# -------------------------------------------
main() {
    require_root
    require_ubuntu_22
    install_base_packages
    install_docker
    setup_firewall
    setup_fail2ban
    setup_data_dirs
    setup_app_dir
    install_systemd_unit

    echo
    green "===================================="
    green "  bootstrap 完成。下一步："
    green "===================================="
    cat <<EOF
  1. 把 .env.production 上传到 /opt/tt-shop/ 并 chmod 600
       scp deploy/env.production.example root@<server>:/opt/tt-shop/.env.production
       # 上去填真实凭证
       ssh root@<server> "chmod 600 /opt/tt-shop/.env.production"

  2. 域名 DNS A 记录指向本机公网 IP（在 Cloudflare 里）：
       tts-api.sailson.com  →  $(curl -fsS https://api.ipify.org 2>/dev/null || echo '<本机公网 IP>')

  3. 启动服务：
       systemctl start tt-shop
       systemctl status tt-shop

  4. 跟踪日志：
       cd /opt/tt-shop
       docker compose -f deploy/docker-compose.prod.yml logs -f api worker

  5. 健康检查：
       curl https://tts-api.sailson.com/health/ready
       # 应返回 {"status":"ok",...}

  6. 部署 cron 备份：
       crontab -e
       # 加一行：
       0 3 * * * /opt/tt-shop/scripts/backup_postgres.sh >> /var/log/tt-shop-backup.log 2>&1
EOF
}

main "$@"
