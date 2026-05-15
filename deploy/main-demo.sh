#!/usr/bin/env bash
# main-demo.sh —— 起/重启 main 分支的常驻 demo 站（业务员框选用 / 「样板间」）。
#
# 在 ECS 上跑。两个容器 + 共享 mysql：
#   doskill-demo-backend     : FastAPI uvicorn :8000（仅内部 doskill-net）
#   doskill-demo-frontend    : Vite dev :5173 → 宿主 ${MAIN_DEMO_FRONTEND_PORT}
#   doskill-mysql (已存在)    : 接到 doskill-net 上、走 'demo' schema
#
# 用法：
#   bash deploy/main-demo.sh            # 起或刷新
#   bash deploy/main-demo.sh --rebuild  # 强制重新 build 镜像（合并后刷新走这个）
set -euo pipefail
log() { printf '\n\033[1;36m[main-demo]\033[0m %s\n' "$*"; }

DEMO_PATH="${DEMO_REPO_PATH:-/opt/doskill/demo}"
NET="${MAIN_DEMO_NET:-doskill-net}"
FRONTEND_PORT="${MAIN_DEMO_FRONTEND_PORT:-5199}"
MYSQL_CONTAINER="${MAIN_DEMO_MYSQL_CONTAINER:-doskill-mysql}"
ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-demopass}"
DB_NAME="${MAIN_DEMO_DB:-demo}"

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

# 1) docker network
docker network inspect "$NET" >/dev/null 2>&1 || {
  log "create network $NET"; docker network create "$NET" >/dev/null
}
docker network connect "$NET" "$MYSQL_CONTAINER" 2>/dev/null || true

# 2) ensure 'demo' schema
log "ensure mysql schema '$DB_NAME'"
docker exec "$MYSQL_CONTAINER" mysql -uroot -p"$ROOT_PASSWORD" \
  -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` DEFAULT CHARACTER SET utf8mb4;" 2>&1 \
  | grep -v "Using a password" || true

# 3) build / refresh images
if [ "$REBUILD" = "1" ] || ! docker image inspect doskill-demo-backend:latest >/dev/null 2>&1; then
  log "build doskill-demo-backend（--network=host 让 pip/npm 共享宿主国内镜像）"
  docker build --network=host \
    --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    -t doskill-demo-backend:latest "$DEMO_PATH/backend"
fi
if [ "$REBUILD" = "1" ] || ! docker image inspect doskill-demo-frontend:latest >/dev/null 2>&1; then
  log "build doskill-demo-frontend"
  docker build --network=host \
    --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
    -t doskill-demo-frontend:latest "$DEMO_PATH/frontend"
fi

# 4) restart containers (always — image may be fresh)
docker rm -f doskill-demo-backend  >/dev/null 2>&1 || true
docker rm -f doskill-demo-frontend >/dev/null 2>&1 || true

log "run doskill-demo-backend"
docker run -d --name doskill-demo-backend \
  --network "$NET" \
  --restart unless-stopped \
  -e DATABASE_URL="mysql+pymysql://root:$ROOT_PASSWORD@$MYSQL_CONTAINER:3306/$DB_NAME" \
  doskill-demo-backend:latest >/dev/null

log "run doskill-demo-frontend (host :$FRONTEND_PORT → container :5173)"
docker run -d --name doskill-demo-frontend \
  --network "$NET" \
  --restart unless-stopped \
  -p "$FRONTEND_PORT:5173" \
  -e VITE_API_URL=http://doskill-demo-backend:8000 \
  doskill-demo-frontend:latest >/dev/null

# 5) wait healthy (frontend serves index.html → 200)
log "wait frontend HTTP 200"
ok=0
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$FRONTEND_PORT/"; then
    ok=1; break
  fi
  sleep 2
done
[ "$ok" = "1" ] || { log "frontend 未就绪，看日志：docker logs doskill-demo-frontend"; exit 1; }

log "✓ main demo up @ http://${PREVIEW_HOST:-localhost}:$FRONTEND_PORT/"
