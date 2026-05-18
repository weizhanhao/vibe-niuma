#!/usr/bin/env bash
# ecs-bootstrap.sh —— 一键在**全新 ECS** 上拉起 vibe-niuma。
# 业务员从扩展助手拷贝以下命令到 ssh 终端（dashscope key 可选）：
#
#   curl -fsSL https://raw.githubusercontent.com/weizhanhao/vibe-niuma/main/deploy/ecs-bootstrap.sh | sudo bash -s -- \
#     --deepseek-key sk-XXXX \
#     [--dashscope-key sk-YYYY] \
#     [--public-host 1.2.3.4]
#
# 做什么：
#   1) 装 git / docker / python3（apt 或 yum 自适配）
#   2) git clone vibe-niuma 到 /opt/vibe-niuma
#   3) cp deploy/env.example → deploy/.env，灌入 key + 公网 IP
#   4) 跑 deploy/deploy.sh —— 本机就是 ECS，inline 步骤（不走 ssh）
#   5) 末尾打印 admin.token 路径 + orchestrator URL
#
# dashscope-key 可选 —— 缺它时视觉模型（看截图理解业务员指什么）会降级失败，
# brainstorm 仍能用 deepseek 文字模型工作，只是看不懂图。业务员要全功能后续可
# 在 deploy/.env 补 DASHSCOPE_API_KEY 再 systemctl restart vibe-niuma-orchestrator。

set -euo pipefail

REPO_URL="${VIBE_NIUMA_REPO_URL:-https://github.com/weizhanhao/vibe-niuma.git}"
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/vibe-niuma}"

DEEPSEEK_KEY=""
DASHSCOPE_KEY=""
PUBLIC_HOST=""

while [ $# -gt 0 ]; do
  case "$1" in
    --deepseek-key)  DEEPSEEK_KEY="$2"; shift 2 ;;
    --dashscope-key) DASHSCOPE_KEY="$2"; shift 2 ;;
    --public-host)   PUBLIC_HOST="$2"; shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

[ -n "$DEEPSEEK_KEY" ] || { echo "缺 --deepseek-key sk-XXXX"; exit 1; }
# dashscope-key 可选 —— 没提供时视觉模型不可用，brainstorm 仍工作

log() { printf '\n\033[1;36m[ecs-bootstrap]\033[0m %s\n' "$*"; }

# ── 0. 必须 root（或 sudo） ─────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  echo "需要 root 权限。请用 sudo 跑：sudo bash $0 ..." >&2
  exit 1
fi

# ── 1. 装系统包 ────────────────────────────────────────────────────
log "装 git / curl / python3 / docker"
if command -v apt-get >/dev/null; then
  apt-get update -y -qq
  apt-get install -y -qq git curl python3 python3-venv python3-pip rsync
  if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
  fi
elif command -v dnf >/dev/null; then
  dnf install -y -q git curl python3 python3-pip rsync
  if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
  fi
elif command -v yum >/dev/null; then
  yum install -y -q git curl python3 python3-pip rsync
  if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
  fi
else
  echo "未识别的包管理器（不是 apt/dnf/yum）" >&2
  exit 1
fi

# ── 2. clone vibe-niuma ───────────────────────────────────────────────
log "git clone $REPO_URL → $DEPLOY_ROOT"
if [ -d "$DEPLOY_ROOT/.git" ]; then
  git -C "$DEPLOY_ROOT" pull --ff-only || true
else
  mkdir -p "$(dirname "$DEPLOY_ROOT")"
  git clone "$REPO_URL" "$DEPLOY_ROOT"
fi
cd "$DEPLOY_ROOT"

# ── 3. 写 deploy/.env ──────────────────────────────────────────────
log "写 deploy/.env（保留 env.example 的默认值）"
cp -n deploy/env.example deploy/.env 2>/dev/null || true

if [ -z "$PUBLIC_HOST" ]; then
  PUBLIC_HOST=$(curl -fsS https://ifconfig.me 2>/dev/null || echo "")
fi
[ -n "$PUBLIC_HOST" ] || PUBLIC_HOST="127.0.0.1"

python3 - "$DEEPSEEK_KEY" "$DASHSCOPE_KEY" "$PUBLIC_HOST" <<'PY'
import sys, re, pathlib
ds, dsc, host = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path("deploy/.env")
text = p.read_text()
def upsert(key, val):
    global text
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={val}"
    text = pat.sub(line, text) if pat.search(text) else text.rstrip() + "\n" + line + "\n"
upsert("DEEPSEEK_API_KEY", ds)
# 空 dashscope key 不写进 .env —— 业务员后续可手动加
if dsc:
    upsert("DASHSCOPE_API_KEY", dsc)
upsert("PREVIEW_HOST", host)
p.write_text(text)
PY

chmod 600 deploy/.env

# ── 4. 部署（本机 inline）──────────────────────────────────────────
# deploy.sh 默认走 ssh + rsync；本机 ECS 不能 ssh 自己，所以重放它的关键逻辑：
log "跑 provision + 本机服务"
set -a; . deploy/.env; set +a

bash deploy/provision.sh || true

# ── orchestrator venv ──────────────────────────────────────────────
log "orchestrator: venv + pip install"
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
{
  echo "DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY"
  [ -n "${DASHSCOPE_API_KEY:-}" ] && echo "DASHSCOPE_API_KEY=$DASHSCOPE_API_KEY"
  echo "STORE_MODEL_IN_DB=False"
} > .env
chmod 600 .env
cd ..

# ── mysql ──────────────────────────────────────────────────────────
mkdir -p mysql
cp -n deploy/mysql/init.sql mysql/init.sql 2>/dev/null || true
docker pull mysql:8 >/dev/null || true
if ! docker inspect vibe-niuma-mysql >/dev/null 2>&1; then
  docker volume create vibe-niuma-mysql-data >/dev/null
  docker run -d --name vibe-niuma-mysql --restart unless-stopped \
    -e MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-demopass}" \
    -p "${MYSQL_PORT:-3306}:3306" \
    -v vibe-niuma-mysql-data:/var/lib/mysql \
    -v "$DEPLOY_ROOT/mysql/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
    mysql:8 >/dev/null
else
  docker start vibe-niuma-mysql >/dev/null 2>&1 || true
fi

# ── systemd ────────────────────────────────────────────────────────
cp deploy/systemd/vibe-niuma-llm-proxy.service /etc/systemd/system/
cp deploy/systemd/vibe-niuma-orchestrator.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vibe-niuma-llm-proxy.service
systemctl enable --now vibe-niuma-orchestrator.service
systemctl restart vibe-niuma-llm-proxy.service vibe-niuma-orchestrator.service

# ── main demo ──────────────────────────────────────────────────────
bash deploy/main-demo.sh || log "main-demo 起失败（不阻塞）"

# ── Plan 11 M4.T29：HTTPS via Caddy + sslip.io ────────────────────
# 给 ECS 公网 IP 拼一个 sslip.io 域名，Caddy 自动找 Let's Encrypt 证书
# 业务员粘 https URL 到扩展，Chrome 不再拦跨域
PUBLIC_DOMAIN="$(echo "$PUBLIC_HOST" | tr '.' '-').sslip.io"
log "装 Caddy + 配 HTTPS (域名=$PUBLIC_DOMAIN)"

if command -v apt-get >/dev/null; then
  if ! command -v caddy >/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y -qq
    apt-get install -y -qq caddy
  fi
elif command -v dnf >/dev/null; then
  dnf install -y -q 'dnf-command(copr)' || true
  dnf copr enable -y @caddy/caddy || true
  dnf install -y -q caddy
elif command -v yum >/dev/null; then
  yum install -y -q yum-plugin-copr || true
  yum copr enable -y @caddy/caddy || true
  yum install -y -q caddy
fi

if command -v caddy >/dev/null; then
  sed "s|{\$PUBLIC_HOST}|$PUBLIC_DOMAIN|g" deploy/Caddyfile > /etc/caddy/Caddyfile
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy
  log "Caddy 已起，HTTPS 域名: https://$PUBLIC_DOMAIN"
else
  log "Caddy 装失败（不阻塞），业务员可以先用 http://$PUBLIC_HOST:9000 应急"
fi

# ── 5. 打印 admin.token + URL ──────────────────────────────────────
TOKEN_PATH="$DEPLOY_ROOT/admin.token"
sleep 3
echo
echo "════════════════════════════════════════════════════════"
echo "  vibe-niuma 部署完成"
if command -v caddy >/dev/null; then
  echo "  Orchestrator URL: https://$PUBLIC_DOMAIN"
  echo "    （首次请求会等 Let's Encrypt 颁发证书，~30s）"
else
  echo "  Orchestrator URL: http://$PUBLIC_HOST:${ORCHESTRATOR_PORT:-9000}"
fi
if [ -f "$TOKEN_PATH" ]; then
  echo "  Admin Token: $(cat "$TOKEN_PATH")"
else
  echo "  Admin Token: orchestrator 还没起好，过 10 秒跑 cat $TOKEN_PATH"
fi
echo "  把以上两项粘到扩展即可。"
echo "  健康检查： bash deploy/healthcheck.sh"
echo "════════════════════════════════════════════════════════"
