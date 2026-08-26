#!/usr/bin/env bash
# vibe-niuma v2 一键 demo。
#
#   ./run-demo.sh            工位隔离 + 冲突三档（无需任何 key，秒级）
#   ./run-demo.sh full       追加真实 AI 复核（需要 DASHSCOPE_API_KEY）
#   ./run-demo.sh serve      起后端 + 前端，浏览器里点
set -euo pipefail
cd "$(dirname "$0")"
MODE="${1:-quick}"

if [ ! -d platform/.venv ]; then
  echo "▸ 初始化 Python 环境…"
  (cd platform && uv venv .venv -q && uv pip install -q --python .venv/bin/python -e ".[dev,redis]")
fi
if [ ! -d platform-skills/dist ]; then
  echo "▸ 构建 skill dist…"
  (cd platform-skills && ./build.sh >/dev/null)
fi
if [ ! -d demo-target/orders-api/.git ]; then
  echo "✗ 缺少 demo 目标仓 demo-target/ —— 见 README"; exit 1
fi

case "$MODE" in
  quick)
    platform/.venv/bin/python platform/scripts/demo.py isolation
    platform/.venv/bin/python platform/scripts/demo.py conflict
    ;;
  full)
    : "${DASHSCOPE_API_KEY:?full 模式需要 DASHSCOPE_API_KEY}"
    platform/.venv/bin/python platform/scripts/demo.py full
    ;;
  serve)
    DB=/tmp/vp-demo.db
    rm -f "$DB"
    VP_DATABASE_URL="sqlite:///$DB" platform/.venv/bin/python platform/scripts/seed_demo.py
    echo
    echo "▸ 后端 http://127.0.0.1:9000   前端 http://127.0.0.1:5173"
    echo "  开发模式认证：请求带 X-User: chen（requester）或 zhao（reviewer）"
    echo
    (cd web && [ -d node_modules ] || npm i --silent)
    VP_DATABASE_URL="sqlite:///$DB" VP_DEV_AUTH=1 \
      platform/.venv/bin/uvicorn vplatform.api.main:app --port 9000 &
    API=$!
    trap 'kill $API 2>/dev/null || true' EXIT
    (cd web && npm run dev)
    ;;
  *) echo "用法：$0 [quick|full|serve]"; exit 2 ;;
esac
