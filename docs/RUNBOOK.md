# RUNBOOK

ECS 上 doskill 各组件的日常运维指引。

## 看日志

```bash
sudo journalctl -u doskill-orchestrator -n 200 -f
sudo journalctl -u doskill-llm-proxy    -n 200 -f
docker logs --tail 200 -f doskill-mysql
docker logs --tail 200 $(docker ps --filter name=doskill-preview- -q | head -1)
```

## 重启

```bash
sudo systemctl restart doskill-orchestrator
sudo systemctl restart doskill-llm-proxy
docker compose -f /opt/doskill/mysql/docker-compose.yml restart
```

## 改配置后生效

`/opt/doskill/.env` 改完：

```bash
sudo systemctl restart doskill-orchestrator doskill-llm-proxy
```

LiteLLM 配置 `/opt/doskill/llm-proxy/config.yml` 改完：

```bash
sudo systemctl restart doskill-llm-proxy
```

## 清理残留预览容器

```bash
# 看哪些还在跑
docker ps --filter name=doskill-preview-

# 全部拆掉（业务员侧的请求会 expired，下次重发）
docker ps -a --filter name=doskill-preview- -q | xargs -r docker rm -f

# 顺便清镜像
docker image ls --filter reference='doskill-preview-*' -q | xargs -r docker rmi -f
```

## 常见故障

| 现象 | 排查 |
|---|---|
| Orchestrator `journalctl` 反复 502/connection refused 给 ANTHROPIC_BASE_URL | llm-proxy 没起 / config 错。`systemctl status doskill-llm-proxy` |
| `failed(building, container)` | `docker logs <handle>` 看为什么没 healthy；端口被占？看 `docker ps -a` |
| `failed(coding, runner-error)` | dev runner 调模型出错。看 orchestrator 日志末尾的 stderr；常见：API key 错 / 模型名 LiteLLM 配置里没列 |
| 预览端口耗尽（`配置端口区间已耗尽`） | 调大 `PREVIEW_PORT_MAX` 或拆掉残留容器 |
| MySQL 连不上 | `docker logs doskill-mysql`；端口被本机占了？改 `MYSQL_PORT` |
| 业务员合并失败 `failed(merging, conflict)` | 期间 main 已变。让业务员丢弃后重新发起即可（设计 §5.5）|

## 风险假设的真实验证（设计文档 §9）

跑过一次真实 E2E 后回填：

- [ ] URL→路由源文件映射的可靠性 —— 4 条路由在动态 `/orders/:id` 上是否正确命中
- [ ] dev runner 凭 brief + 截图能否产出可用改动
- [ ] Docker 预览启动延迟是否可忍
