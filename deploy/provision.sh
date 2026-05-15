#!/usr/bin/env bash
# provision.sh —— ECS 初始化（幂等）。在 ECS 上跑一次即可：
#   bash deploy/provision.sh
#
# 装：docker + docker compose + python3.11+venv + node + git + mysql-client。
# 已装的跳过；失败立即退出；每步打印进度。
set -euo pipefail

log() { printf '\n\033[1;36m[provision]\033[0m %s\n' "$*"; }

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/doskill}"

# ── 检测发行版 ─────────────────────────────────────────────────────
if [ -r /etc/os-release ]; then . /etc/os-release; fi
ID_LIKE_FAMILY="${ID_LIKE:-}${ID:-}"

if echo "$ID_LIKE_FAMILY" | grep -qE 'debian|ubuntu'; then
  PKG=apt
elif echo "$ID_LIKE_FAMILY" | grep -qE 'rhel|centos|alibaba|alinux'; then
  PKG=yum
else
  log "未识别的发行版（${ID:-?}），按 yum 处理"; PKG=yum
fi
log "包管理器：$PKG"

ensure_pkg() {  # ensure_pkg <bin> <package_name>
  local bin="$1" pkg="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    log "已装：$bin"
    return
  fi
  log "装：$pkg"
  if [ "$PKG" = "apt" ]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq "$pkg"
  else
    sudo yum install -y -q "$pkg"
  fi
}

# ── docker + compose plugin ───────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  log "装 docker（best-effort；如失败请按官方文档手动安装后重跑）"
  if [ "$PKG" = "apt" ]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io docker-compose-plugin
  else
    sudo yum install -y -q docker
  fi
  sudo systemctl enable --now docker
fi
sudo systemctl is-active docker >/dev/null || sudo systemctl start docker
log "docker 就绪：$(docker --version)"

# ── docker registry 镜像加速（国内 ECS 拉不到 Docker Hub）────────
USE_DOCKER_MIRROR="${USE_DOCKER_MIRROR:-1}"
if [ "$USE_DOCKER_MIRROR" = "1" ]; then
  DAEMON=/etc/docker/daemon.json
  WANT='{"registry-mirrors":["https://docker.m.daocloud.io","https://docker.1ms.run","https://hub-mirror.c.163.com"]}'
  if [ ! -f "$DAEMON" ] || ! sudo grep -q 'registry-mirrors' "$DAEMON"; then
    log "配置 docker registry 镜像加速 → $DAEMON"
    sudo mkdir -p /etc/docker
    echo "$WANT" | sudo tee "$DAEMON" >/dev/null
    sudo systemctl restart docker
    sleep 2
  else
    log "docker registry 镜像已配置，跳过"
  fi
fi

# ── python / venv / pip ────────────────────────────────────────────
ensure_pkg python3 python3
ensure_pkg pip3 python3-pip
if ! python3 -c 'import venv' 2>/dev/null; then
  if [ "$PKG" = "apt" ]; then sudo apt-get install -y -qq python3-venv; fi
fi
log "python：$(python3 --version)"

# ── node / npm ─────────────────────────────────────────────────────
# 优先用发行版自带（alinux 4 有 nodejs 22；ubuntu 22.04 有 nodejs 18+）。
# 装不上再 fallback 到 NodeSource 20.x。
if ! command -v node >/dev/null 2>&1; then
  log "装 node（先试发行版自带）"
  INSTALL_OK=0
  if [ "$PKG" = "apt" ]; then
    sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm && INSTALL_OK=1
  else
    sudo dnf install -y -q nodejs npm && INSTALL_OK=1 || sudo yum install -y -q nodejs npm && INSTALL_OK=1
  fi
  if [ "$INSTALL_OK" != "1" ] || ! command -v node >/dev/null 2>&1; then
    log "发行版自带不可用，回退 NodeSource 20.x"
    if [ "$PKG" = "apt" ]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
      sudo apt-get install -y -qq nodejs
    else
      curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo -E bash -
      sudo yum install -y -q nodejs
    fi
  fi
fi
log "node：$(node --version) · npm：$(npm --version)"

ensure_pkg git git
ensure_pkg rsync rsync
if ! command -v mysql >/dev/null 2>&1; then
  if [ "$PKG" = "apt" ]; then sudo apt-get install -y -qq mysql-client; else sudo yum install -y -q mysql; fi
fi

# ── 国内镜像（按 .env 里的 USE_*_MIRROR 决定）─────────────────────
USE_NPM_MIRROR="${USE_NPM_MIRROR:-1}"
USE_PIP_MIRROR="${USE_PIP_MIRROR:-1}"
if [ "$USE_NPM_MIRROR" = "1" ]; then
  log "配置 npm registry → npmmirror.com"
  npm config set registry https://registry.npmmirror.com
fi
if [ "$USE_PIP_MIRROR" = "1" ]; then
  log "配置 pip → 清华镜像"
  mkdir -p ~/.pip
  cat > ~/.pip/pip.conf <<'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
EOF
fi

# ── 部署目录 ─────────────────────────────────────────────────────
sudo mkdir -p "$DEPLOY_ROOT" "$DEPLOY_ROOT/orchestrator" "$DEPLOY_ROOT/demo" \
              "$DEPLOY_ROOT/llm-proxy" "$DEPLOY_ROOT/mysql"
sudo chown -R "$(id -u):$(id -g)" "$DEPLOY_ROOT"
log "部署目录：$DEPLOY_ROOT"

log "完成。下一步在本机跑 deploy/deploy.sh --full"
