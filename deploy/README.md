# doskill ECS 部署

## 一次跑通

在本机：

```bash
cp deploy/env.example deploy/.env
$EDITOR deploy/.env                  # 填 ECS 地址 / SSH key / LLM_API_KEY 等

bash deploy/deploy.sh --full         # 第一次：含 provision
bash deploy/healthcheck.sh           # 验证

# 真实 E2E 冒烟（在 ECS 上）
ssh user@ecs "cd /opt/doskill/orchestrator && DOSKILL_E2E=1 venv/bin/pytest -m e2e -v -s"
```

之后每次发布只需：

```bash
bash deploy/deploy.sh                # 仅投代码 + 重启服务
bash deploy/healthcheck.sh
```

## 各组件

| 组件 | 怎么起 | 端口 | 数据/状态 |
|---|---|---|---|
| MySQL | `docker compose -f mysql/docker-compose.yml up -d` | `${MYSQL_PORT}` | `doskill-mysql-data` 卷 |
| LiteLLM proxy | `systemctl start doskill-llm-proxy` | `${LLM_PROXY_PORT}` | 配置 `llm-proxy/config.yml` |
| Orchestrator | `systemctl start doskill-orchestrator` | `${ORCHESTRATOR_PORT}` | MySQL `orchestrator` 库 |
| 预览容器 | 由 Orchestrator 按需起 / 拆 | `${PREVIEW_PORT_MIN}..${PREVIEW_PORT_MAX}` | 容器内 |
| demo 仓库 | `${DEMO_REPO_PATH}` | — | git，每条变更一个 `cr/<id>` 分支 |

## 模型走哪

```
claude-code 子进程
   │  ANTHROPIC_BASE_URL=http://127.0.0.1:${LLM_PROXY_PORT}
   ▼
LiteLLM proxy  →  DeepSeek / 通义千问 / Claude（按 config.yml）
   ▲
BrainstormingSkill 直接打它的 OpenAI 路径
```

## 排查

- `journalctl -u doskill-orchestrator -f`
- `journalctl -u doskill-llm-proxy -f`
- `docker logs doskill-mysql`
- `docker ps --filter name=doskill-preview-`
- 见 `docs/RUNBOOK.md`。

## 不做（MVP 边界）

不上 nginx / HTTPS / 防火墙调优 / 多租户 / 容器加固 —— 设计文档 §2 / §8 明确不考虑。
