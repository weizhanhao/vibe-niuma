#!/usr/bin/env bash
# deploy.sh —— 投递代码到 ECS + 装依赖 + 重启服务（幂等）。
#
# 用法（在本机跑）：
#   bash deploy/deploy.sh             # 仅投代码 + 重启
#   bash deploy/deploy.sh --full      # 含 provision.sh
#
# 读 deploy/.env 拿到 ECS 地址 / SSH key / 各端口。
set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"
[ -f "$ENV_FILE" ] || { echo "缺 deploy/.env，参考 deploy/env.example"; exit 1; }
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

: "${ECS_HOST:?需要 ECS_HOST}"
: "${ECS_USER:?需要 ECS_USER}"
: "${DEPLOY_ROOT:?需要 DEPLOY_ROOT}"

SSH_KEY_EXPANDED="${ECS_SSH_KEY/#~/$HOME}"
SSH=(ssh -i "$SSH_KEY_EXPANDED" -o StrictHostKeyChecking=accept-new "$ECS_USER@$ECS_HOST")
RSYNC_E="ssh -i $SSH_KEY_EXPANDED -o StrictHostKeyChecking=accept-new"

log() { printf '\n\033[1;36m[deploy]\033[0m %s\n' "$*"; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── 可选：先 provision ────────────────────────────────────────────
if [ "${1:-}" = "--full" ]; then
  log "投递 provision.sh + .env，跑 provision.sh"
  "${SSH[@]}" "mkdir -p $DEPLOY_ROOT"
  rsync -az -e "$RSYNC_E" "$ENV_FILE" "$ECS_USER@$ECS_HOST:$DEPLOY_ROOT/.env"
  rsync -az -e "$RSYNC_E" "$REPO_ROOT/deploy/" "$ECS_USER@$ECS_HOST:$DEPLOY_ROOT/deploy/"
  "${SSH[@]}" "cd $DEPLOY_ROOT && set -a && . .env && set +a && bash deploy/provision.sh"
fi

# ── 投代码 ─────────────────────────────────────────────────────
log "rsync orchestrator + demo + deploy → $ECS_HOST"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude 'venv' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'dist' \
  -e "$RSYNC_E" \
  "$REPO_ROOT/orchestrator/" "$ECS_USER@$ECS_HOST:$DEPLOY_ROOT/orchestrator/"
rsync -az --delete \
  --exclude 'node_modules' --exclude 'dist' --exclude '__pycache__' \
  -e "$RSYNC_E" \
  "$REPO_ROOT/demo/" "$ECS_USER@$ECS_HOST:$DEPLOY_ROOT/demo/"
rsync -az --delete -e "$RSYNC_E" \
  "$REPO_ROOT/deploy/" "$ECS_USER@$ECS_HOST:$DEPLOY_ROOT/deploy/"
rsync -az -e "$RSYNC_E" "$ENV_FILE" "$ECS_USER@$ECS_HOST:$DEPLOY_ROOT/.env"

# ── ECS 上：装依赖 + 起服务 ────────────────────────────────────────
"${SSH[@]}" bash -se <<EOF
set -euo pipefail
log() { printf '\n\033[1;33m[ecs]\033[0m %s\n' "\$*"; }
cd $DEPLOY_ROOT
set -a && . .env && set +a

# Orchestrator venv
log "Orchestrator: venv + pip install"
cd orchestrator
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q -e ".[dev]"
cd ..

# demo 仓库：根据 DEMO_GIT_REMOTE 决定 clone vs 本地 git init
if [ -n "\${DEMO_GIT_REMOTE:-}" ]; then
  if [ ! -d "\$DEMO_REPO_PATH/.git" ]; then
    log "git clone \$DEMO_GIT_REMOTE → \$DEMO_REPO_PATH"
    rm -rf "\$DEMO_REPO_PATH"
    git clone "\$DEMO_GIT_REMOTE" "\$DEMO_REPO_PATH"
  else
    log "demo 仓库已存在，git pull"
    git -C "\$DEMO_REPO_PATH" pull --ff-only || true
  fi
else
  if [ ! -d "\$DEMO_REPO_PATH/.git" ]; then
    log "demo 没有远端：在 \$DEMO_REPO_PATH 内 git init + 初始 commit"
    cd "\$DEMO_REPO_PATH"
    git init -q -b main
    git config user.email "doskill@local"
    git config user.name  "doskill"
    git add -A
    git -c init.defaultBranch=main commit -q -m "demo init" || true
    cd -
  fi
fi

# demo 前端构建（预热 docker build 用的依赖；可选）
log "demo 前端 npm ci + build"
cd "\$DEMO_REPO_PATH/frontend"
npm ci --silent --no-audit --no-fund || npm install --silent --no-audit --no-fund
npm run build --silent
cd "$DEPLOY_ROOT"

# llm-proxy venv（只装一次 litellm）
log "llm-proxy: venv + pip install litellm[proxy]"
cd llm-proxy
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q "litellm[proxy]"
[ -f config.yml ] || cp config.example.yml config.yml
cd ..

# MySQL compose
log "MySQL compose up -d"
cd mysql
docker compose up -d
cd ..

# systemd units
log "安装 + 重启 systemd units"
sudo cp deploy/systemd/doskill-llm-proxy.service /etc/systemd/system/
sudo cp deploy/systemd/doskill-orchestrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now doskill-llm-proxy.service
sudo systemctl enable --now doskill-orchestrator.service
sudo systemctl restart doskill-llm-proxy.service doskill-orchestrator.service

log "完成；用 deploy/healthcheck.sh 验证"
EOF

log "本机：scp 完。下一步：bash deploy/healthcheck.sh"
