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
  log "bootstrap rsync（新装的 ECS 不一定预装）"
  "${SSH[@]}" 'command -v rsync >/dev/null 2>&1 || { command -v dnf >/dev/null && dnf install -y -q rsync; } || { command -v yum >/dev/null && yum install -y -q rsync; } || { command -v apt-get >/dev/null && apt-get install -y -qq rsync; }'
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
# ⚠ demo 不能 --delete + 不排 .git：会把 cr/* 分支和合并到 main 的 commit 全擦掉。
# .git 是 ECS 上 orchestrator pipeline 写的真相，源端没有；--update 也是为了避免
# 本机老源码盖掉 dev runner 刚改过的工作树（合并后 main 上的内容仍由源端覆盖回去
# 就丢工作了）。AGENTS.md 由 orchestrator 自己生成，也排掉。
rsync -az --update \
  --exclude '.git' --exclude 'AGENTS.md' \
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
    # 用 -C 避免 cd/safe.directory 类的玄学
    git config --global --add safe.directory "\$DEMO_REPO_PATH" || true
    git init -q -b main "\$DEMO_REPO_PATH"
    git -C "\$DEMO_REPO_PATH" config user.email "doskill@local"
    git -C "\$DEMO_REPO_PATH" config user.name  "doskill"
    git -C "\$DEMO_REPO_PATH" add -A
    git -C "\$DEMO_REPO_PATH" commit -q -m "demo init" || true
  fi
fi

# demo 前端构建（预热 docker build 用的依赖；可选）
log "demo 前端 npm ci + build"
cd "\$DEMO_REPO_PATH/frontend"
npm ci --silent --no-audit --no-fund || npm install --silent --no-audit --no-fund
npm run build --silent
cd "$DEPLOY_ROOT"

# llm-proxy venv（只装一次 litellm + prisma 哑装一下，免得 1.84 异常处理器 crash）
log "llm-proxy: venv + pip install litellm[proxy] + prisma"
# 把 deploy/llm-proxy/ 内容拷到工作目录（systemd 的 WorkingDirectory）
cp -n deploy/llm-proxy/* llm-proxy/ 2>/dev/null || true
cd llm-proxy
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q "litellm[proxy]" prisma
[ -f config.yml ] || cp config.example.yml config.yml
# litellm 启动时会 load_dotenv() 从 CWD 向上爬 → 会捞到 /opt/doskill/.env 里
# 给 orchestrator 用的 DATABASE_URL，触发 prisma DB 初始化 crash。
# 在 llm-proxy CWD 放一份只含 provider key 的 .env，load_dotenv 找到这个就停。
cat > .env <<INNER_ENV
DEEPSEEK_API_KEY=\$DEEPSEEK_API_KEY
DASHSCOPE_API_KEY=\$DASHSCOPE_API_KEY
STORE_MODEL_IN_DB=False
INNER_ENV
chmod 600 .env
cd ..

# init.sql 用于首次起容器时初始化 schema
cp -n deploy/mysql/init.sql mysql/init.sql 2>/dev/null || true

# MySQL 容器（plain docker run，省掉 compose 依赖）
log "MySQL: 拉镜像 + 起容器（doskill-mysql）"
docker pull mysql:8 >/dev/null || true
# 已存在就跳过创建；否则起一个新的
if ! docker inspect doskill-mysql >/dev/null 2>&1; then
  docker volume create doskill-mysql-data >/dev/null
  docker run -d --name doskill-mysql \
    --restart unless-stopped \
    -e MYSQL_ROOT_PASSWORD="\$MYSQL_ROOT_PASSWORD" \
    -p "\$MYSQL_PORT:3306" \
    -v doskill-mysql-data:/var/lib/mysql \
    -v "\$DEPLOY_ROOT/mysql/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
    mysql:8 >/dev/null
else
  docker start doskill-mysql >/dev/null 2>&1 || true
fi

# systemd units
log "安装 + 重启 systemd units"
sudo cp deploy/systemd/doskill-llm-proxy.service /etc/systemd/system/
sudo cp deploy/systemd/doskill-orchestrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now doskill-llm-proxy.service
sudo systemctl enable --now doskill-orchestrator.service
sudo systemctl restart doskill-llm-proxy.service doskill-orchestrator.service

# main demo 站（业务员框选「样板间」）
log "起 main 分支常驻 demo 站"
bash deploy/main-demo.sh || log "main-demo 起失败（不阻塞主部署，看 docker logs）"

log "完成；用 deploy/healthcheck.sh 验证"
EOF

log "本机：scp 完。下一步：bash deploy/healthcheck.sh"
