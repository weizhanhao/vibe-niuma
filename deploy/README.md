# vibe-niuma ECS 部署

## 一次跑通

在本机：

```bash
cp deploy/env.example deploy/.env
$EDITOR deploy/.env                  # 填 ECS 地址 / SSH key / LLM_API_KEY 等

bash deploy/deploy.sh --full         # 第一次：含 provision
bash deploy/healthcheck.sh           # 验证

# 真实 E2E 冒烟（在 ECS 上）
ssh user@ecs "cd /opt/vibe-niuma/orchestrator && VIBE_NIUMA_E2E=1 venv/bin/pytest -m e2e -v -s"
```

之后每次发布只需：

```bash
bash deploy/deploy.sh                # 仅投代码 + 重启服务（前会自动备份当前版本）
bash deploy/healthcheck.sh
```

## 回滚

每次 `deploy.sh` 都会把 ECS 上的 `orchestrator/` + `llm-proxy/` 备份到 `*.prev/`，
出问题直接：

```bash
bash deploy/rollback.sh              # 交互式：先 show 当前 vs prev，y 确认
bash deploy/rollback.sh -y           # 紧急时跳过确认
```

回滚不动数据库（Plan 9 加列加表是 backward-compat 的）。回滚后当前版本会保留在
`*.broken/` 现场调查用，下次 deploy 时被新 prev 覆盖。

查 ECS 上当前在跑哪个版本：

```bash
ssh ${ECS_USER}@${ECS_HOST} 'cat /opt/vibe-niuma/RELEASE_INFO'
```

## 各组件

| 组件 | 怎么起 | 端口 | 数据/状态 |
|---|---|---|---|
| MySQL | `docker compose -f mysql/docker-compose.yml up -d` | `${MYSQL_PORT}` | `vibe-niuma-mysql-data` 卷 |
| LiteLLM proxy | `systemctl start vibe-niuma-llm-proxy` | `${LLM_PROXY_PORT}` | 配置 `llm-proxy/config.yml` |
| Orchestrator | `systemctl start vibe-niuma-orchestrator` | `${ORCHESTRATOR_PORT}` | MySQL `orchestrator` 库 |
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

- `journalctl -u vibe-niuma-orchestrator -f`
- `journalctl -u vibe-niuma-llm-proxy -f`
- `docker logs vibe-niuma-mysql`
- `docker ps --filter name=vibe-niuma-preview-`
- 见 `docs/RUNBOOK.md`。

## 不做（MVP 边界）

不上 nginx / HTTPS / 防火墙调优 / 多租户 / 容器加固 —— 设计文档 §2 / §8 明确不考虑。
