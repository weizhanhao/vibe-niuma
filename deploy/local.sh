#!/usr/bin/env bash
# local.sh —— 在**本机**起一套 vibe-niuma（mysql + llm-proxy + orchestrator），
# 不 ssh、不 rsync。给业务员先在 Mac/Linux/WSL 上跑通用的。
#
# 用法：
#   bash deploy/local.sh           # 全套起
#   bash deploy/local.sh --stop    # 停所有本地进程
#   bash deploy/local.sh --reset   # 拆掉 docker container/volume 重起（保留 venv）
#
# 前提：
#   - docker 已装（hub.docker.com）
#   - python3.10+ 已装
#   - deploy/.env 已写（至少 DEEPSEEK_API_KEY；其他用默认值）
#
# 完成后：
#   - orchestrator 在 http://127.0.0.1:9000
#   - llm-proxy   在 http://127.0.0.1:8787
#   - mysql       在 127.0.0.1:3306
#   - admin.token 在 $REPO_ROOT/admin.token，用 cat 拿
#   - 跑 LOCAL=1 bash deploy/healthcheck.sh 验证

set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"
[ -f "$ENV_FILE" ] || { echo "缺 deploy/.env，参考 deploy/env.example"; exit 1; }
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n\033[1;36m[local]\033[0m %s\n' "$*"; }

PID_DIR="$REPO_ROOT/.local-pids"
mkdir -p "$PID_DIR"

# ── --stop：停后台进程 ──────────────────────────────────────────────
if [ "${1:-}" = "--stop" ]; then
  for pidfile in "$PID_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid=$(cat "$pidfile")
    if kill "$pid" 2>/dev/null; then
      log "停 $(basename "$pidfile" .pid)（pid $pid）"
    fi
    rm -f "$pidfile"
  done
  exit 0
fi

# ── --reset：拆 docker 重来 ─────────────────────────────────────────
if [ "${1:-}" = "--reset" ]; then
  log "拆 docker container / volume / token（保留 venv）"
  docker rm -f vibe-niuma-mysql 2>/dev/null || true
  docker volume rm vibe-niuma-mysql-data 2>/dev/null || true
  rm -f "$REPO_ROOT/admin.token"
fi

# ── orchestrator venv ──────────────────────────────────────────────
log "Orchestrator: venv + pip install"
cd orchestrator
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q -e ".[dev]"
cd ..

# ── llm-proxy venv ─────────────────────────────────────────────────
log "llm-proxy: venv + pip install litellm[proxy]"
mkdir -p llm-proxy
cp -n deploy/llm-proxy/* llm-proxy/ 2>/dev/null || true
cd llm-proxy
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q "litellm[proxy]" prisma
[ -f config.yml ] || cp config.example.yml config.yml
# 隔离 .env 避免 litellm load_dotenv 撞 orchestrator 的 DATABASE_URL
cat > .env <<INNER_ENV
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY
DASHSCOPE_API_KEY=$DASHSCOPE_API_KEY
STORE_MODEL_IN_DB=False
INNER_ENV
chmod 600 .env
cd ..

# ── mysql ──────────────────────────────────────────────────────────
log "MySQL: 拉镜像 + 起容器（vibe-niuma-mysql）"
mkdir -p mysql
cp -n deploy/mysql/init.sql mysql/init.sql 2>/dev/null || true
docker pull mysql:8 >/dev/null || true
if ! docker inspect vibe-niuma-mysql >/dev/null 2>&1; then
  docker volume create vibe-niuma-mysql-data >/dev/null
  docker run -d --name vibe-niuma-mysql \
    --restart unless-stopped \
    -e MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-demopass}" \
    -p "${MYSQL_PORT:-3306}:3306" \
    -v vibe-niuma-mysql-data:/var/lib/mysql \
    -v "$REPO_ROOT/mysql/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
    mysql:8 >/dev/null
else
  docker start vibe-niuma-mysql >/dev/null 2>&1 || true
fi

# ── demo repo ──────────────────────────────────────────────────────
DEMO_LOCAL="${DEMO_REPO_PATH:-$REPO_ROOT/demo}"
if [ ! -d "$DEMO_LOCAL/.git" ]; then
  log "demo 没有 .git：在 $DEMO_LOCAL 内 git init + 初始 commit"
  git -C "$DEMO_LOCAL" init -q -b main
  git -C "$DEMO_LOCAL" config user.email "vibe-niuma@local"
  git -C "$DEMO_LOCAL" config user.name  "vibe-niuma"
  git -C "$DEMO_LOCAL" add -A
  git -C "$DEMO_LOCAL" commit -q -m "demo init" || true
fi

# ── admin.token ────────────────────────────────────────────────────
TOKEN_FILE="$REPO_ROOT/admin.token"
if [ ! -f "$TOKEN_FILE" ]; then
  log "生成 admin.token"
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi

# ── 起后台进程 ──────────────────────────────────────────────────────
start_bg() {
  local name="$1" cmd="$2"
  local pidfile="$PID_DIR/$name.pid"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    log "$name 已在跑（pid $(cat "$pidfile")），跳过"
    return
  fi
  log "起 $name"
  ( cd "$REPO_ROOT" && eval "$cmd" ) >> "$PID_DIR/$name.log" 2>&1 &
  echo $! > "$pidfile"
}

start_bg llm-proxy \
  "ADMIN_TOKEN_PATH=$TOKEN_FILE llm-proxy/venv/bin/litellm --config llm-proxy/config.yml --port ${LLM_PROXY_PORT:-8787}"

start_bg orchestrator \
  "ADMIN_TOKEN_PATH=$TOKEN_FILE DATABASE_URL='mysql+pymysql://root:${MYSQL_ROOT_PASSWORD:-demopass}@127.0.0.1:${MYSQL_PORT:-3306}/orchestrator' DEMO_REPO_PATH=$DEMO_LOCAL PREVIEW_HOST=localhost orchestrator/venv/bin/uvicorn orchestrator.main:app --host 127.0.0.1 --port ${ORCHESTRATOR_PORT:-9000}"

log "等服务起来…"
sleep 4

echo
echo "  [local] ✓ 起完。验证："
echo "  [local]   LOCAL=1 bash deploy/healthcheck.sh"
echo "  [local] Admin Token："
echo "  [local]   cat $TOKEN_FILE"
echo "  [local] 把 URL=http://127.0.0.1:9000 + 这个 token 粘到扩展即可。"
echo "  [local] 停服务： bash deploy/local.sh --stop"
