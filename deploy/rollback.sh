#!/usr/bin/env bash
# rollback.sh —— 一键回滚到上一次部署的版本。
#
# 用法（在本机跑）：
#   bash deploy/rollback.sh           # 交互式确认；显示 current vs prev
#   bash deploy/rollback.sh -y        # 跳过确认（CI/紧急）
#
# 原理：deploy.sh 每次投代码前会把 ECS 上的 orchestrator/ 和 llm-proxy/ tar 到
# *.prev/。回滚就是把当前 mv 成 *.broken/（保留现场），把 *.prev/ mv 回当前位置，
# 重装 venv 依赖（pyproject 可能改了），重启 systemd。
#
# 数据库：不动。Plan 9 加列加表是 backward-compat 的，老代码不读不写新列即可。
# 如果未来有 destructive migration，需要在这里加 `alembic downgrade -1`。
#
# 不变量：
#   - 没 *.prev/ 时拒绝跑（说明没部署过 → 没东西回滚）
#   - mv 失败时立刻退出，不会半破坏（broken/ 仍在，可手工恢复）
#   - rollback 完会再写一份 RELEASE_INFO 标 rolled_back=true，方便审计

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

log() { printf '\n\033[1;36m[rollback]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[rollback]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[rollback]\033[0m %s\n' "$*" >&2; exit 1; }

AUTO=""
if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then AUTO=1; fi

# ── 1) 显示当前 vs 备份 ─────────────────────────────────────────
log "查 ECS 上的版本信息"
"${SSH[@]}" bash -se <<EOF || die "ssh 失败"
set -euo pipefail
cd $DEPLOY_ROOT
echo "── 当前 RELEASE_INFO ──"
[ -f RELEASE_INFO ] && cat RELEASE_INFO || echo "(没有 RELEASE_INFO；可能是首版本)"
echo
echo "── 上一版 RELEASE_INFO.prev ──"
[ -f RELEASE_INFO.prev ] && cat RELEASE_INFO.prev || echo "(没有 RELEASE_INFO.prev)"
echo
echo "── 备份目录是否存在 ──"
for d in orchestrator.prev llm-proxy.prev; do
  if [ -d "\$d" ]; then
    echo "  ✓ \$d/ 存在"
  else
    echo "  ✗ \$d/ 缺失"
  fi
done
EOF

# ── 2) 确认 ────────────────────────────────────────────────────
if [ -z "$AUTO" ]; then
  printf '\n继续回滚？[y/N] '
  read -r ans
  case "$ans" in y|Y|yes|YES) ;; *) die "已取消";; esac
fi

# ── 3) 真正回滚 ──────────────────────────────────────────────
log "ECS: 真正回滚 + 重装依赖 + 重启服务"
"${SSH[@]}" bash -se <<EOF || die "回滚过程出错；服务可能处于中间态，立刻 ssh 上去看"
set -euo pipefail
cd $DEPLOY_ROOT

for d in orchestrator.prev llm-proxy.prev; do
  if [ ! -d "\$d" ]; then
    echo "✗ \$d/ 不存在，没有可回滚的版本（deploy.sh 没跑过、或备份被手动删了）"
    exit 1
  fi
done

# 当前移到 *.broken/（保留现场；下次 deploy 时被 *.prev 覆盖）
for dir in orchestrator llm-proxy; do
  if [ -d "\$dir" ]; then
    rm -rf "\$dir.broken"
    mv "\$dir" "\$dir.broken"
    echo "  当前 \$dir/ → \$dir.broken/"
  fi
  mv "\$dir.prev" "\$dir"
  echo "  \$dir.prev/ → \$dir/"
done

# venv 不在备份里 → 重装
echo "重装 orchestrator venv"
cd orchestrator
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q -e ".[dev]"
cd ..

echo "重装 llm-proxy venv"
cd llm-proxy
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -U pip wheel
venv/bin/pip install -q "litellm[proxy]" prisma
cd ..

# RELEASE_INFO 落回上一版 + 标 rolled_back
if [ -f RELEASE_INFO.prev ]; then
  cp RELEASE_INFO RELEASE_INFO.broken 2>/dev/null || true
  cp RELEASE_INFO.prev RELEASE_INFO
  echo "rolled_back_at=\$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> RELEASE_INFO
fi

echo "重启 systemd units"
sudo systemctl daemon-reload || true
sudo systemctl restart vibe-niuma-llm-proxy.service vibe-niuma-orchestrator.service

# 等 5s 给服务起来；不健康就 fail（避免误报 success）
sleep 5
systemctl is-active --quiet vibe-niuma-orchestrator.service || { echo "✗ orchestrator 未起来"; exit 1; }
systemctl is-active --quiet vibe-niuma-llm-proxy.service   || { echo "✗ llm-proxy 未起来"; exit 1; }
echo "✓ 服务都 active"
EOF

# ── 4) 本地健康检查 ────────────────────────────────────────────
log "跑 healthcheck"
bash "$(dirname "$0")/healthcheck.sh" || warn "healthcheck 失败，但服务已起；ssh 上去查 journalctl"

log "✓ 回滚完成；当前 RELEASE_INFO 见 ECS 上 $DEPLOY_ROOT/RELEASE_INFO"
log "现场保留在 ECS 上 $DEPLOY_ROOT/{orchestrator,llm-proxy}.broken/，下次 deploy 会被新 prev 覆盖"
