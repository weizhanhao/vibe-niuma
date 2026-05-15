#!/usr/bin/env bash
# healthcheck.sh —— 部署后一键体检。本机跑：
#   bash deploy/healthcheck.sh
set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"
[ -f "$ENV_FILE" ] || { echo "缺 deploy/.env"; exit 1; }
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

: "${ECS_HOST:?需要 ECS_HOST}"
: "${ECS_USER:?需要 ECS_USER}"
SSH_KEY_EXPANDED="${ECS_SSH_KEY/#~/$HOME}"
SSH=(ssh -i "$SSH_KEY_EXPANDED" -o StrictHostKeyChecking=accept-new "$ECS_USER@$ECS_HOST")

PASS=0; FAIL=0
check() {
  local name="$1" cmd="$2"
  # 注意：不用 `bash -c "$cmd"`，SSH 会把后续 argv 用空格拼起来再
  # 交给远端 shell，丢掉外层引号 → 管道被本地化（远端的 `bash -c ss`
  # 只会跑没参数的 ss，抓不到 listening）。直接把整条命令作为单参 SSH。
  if "${SSH[@]}" "$cmd" >/dev/null 2>&1; then
    printf '  \033[32m✓\033[0m %s\n' "$name"; PASS=$((PASS+1))
  else
    printf '  \033[31m✗\033[0m %s\n' "$name"; FAIL=$((FAIL+1))
  fi
}

echo "doskill 健康检查 → $ECS_HOST"
check "MySQL 端口 ${MYSQL_PORT:-3306} 可连"          "ss -ltn | grep -q ':${MYSQL_PORT:-3306} '"
check "llm-proxy 端口 ${LLM_PROXY_PORT:-8787} 可连"   "ss -ltn | grep -q ':${LLM_PROXY_PORT:-8787} '"
check "Orchestrator /health 200"                      "curl -fsS http://127.0.0.1:${ORCHESTRATOR_PORT:-9000}/health | grep -q ok"
check "demo 仓库存在 + 有 main 分支"                  "test -d ${DEMO_REPO_PATH:-/opt/doskill/demo}/.git && git -C ${DEMO_REPO_PATH:-/opt/doskill/demo} show-ref --verify refs/heads/main"
check "docker daemon 可用"                            "docker info >/dev/null"
check "doskill-orchestrator.service active"           "systemctl is-active doskill-orchestrator.service"
check "doskill-llm-proxy.service active"              "systemctl is-active doskill-llm-proxy.service"
check "main demo 容器 doskill-demo-backend up"        "docker inspect -f '{{.State.Running}}' doskill-demo-backend | grep -q true"
check "main demo 容器 doskill-demo-frontend up"       "docker inspect -f '{{.State.Running}}' doskill-demo-frontend | grep -q true"
check "main demo 端口 ${MAIN_DEMO_FRONTEND_PORT:-5199} 可连" "ss -ltn | grep -q ':${MAIN_DEMO_FRONTEND_PORT:-5199} '"

# 11. admin API 鉴权通（Plan 6）：用 ECS 上的 admin.token 调 /admin/config，期望 200。
#     远端跑：避开本机必须能直连 9000 的限制；token 通过命令替换内联进 curl 头部。
ADMIN_TOKEN_PATH="${DEPLOY_ROOT:-/opt/doskill}/admin.token"
check "admin /admin/config 鉴权 200" \
  "test -s $ADMIN_TOKEN_PATH && curl -fsS -o /dev/null -H \"X-Admin-Token: \$(cat $ADMIN_TOKEN_PATH)\" http://127.0.0.1:${ORCHESTRATOR_PORT:-9000}/admin/config"

printf '\n  通过 %d · 失败 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
