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
  if "${SSH[@]}" bash -c "$cmd" >/dev/null 2>&1; then
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

printf '\n  通过 %d · 失败 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
