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

2026-05-15 首次真实 E2E 闭环跑通（114.55.171.64 / Alibaba Cloud Linux 4 / opencode + deepseek/deepseek-v4-flash），结论：

- [x] **URL→路由源文件映射** —— `/settings` 命中 `frontend/src/pages/Settings.tsx`，正则 + import 解析 OK。`/orders/:id` 动态段还没真正打过（建议下一轮补一次）。
- [x] **dev runner 凭 brief 产出可用改动** —— "把保存按钮改成立即保存" 一次 commit、零返工。改动落在 `cr/<id>` 分支后被自动合并到 main。
- [x] **Docker 预览启动延迟** —— 全闭环（clarifying → coding → building → preview-ready → merged）耗时 **2 分 43 秒**，预览容器 build + healthy 约 60–80s，业务员等到的「按钮变了」典型 < 3 分钟。可忍。

补充教训（不在设计预期内但跑下来发现的）：
- 用 claude-code CLI 时跟 **DeepSeek hybrid thinking mode** 协议不兼容（要求把 `reasoning_content` 回传，claude-code 不带）。默认改用 **opencode**；想用 claude-code 就走真 Claude key + Claude 模型，别走 DeepSeek。
- 国内 ECS 上 GitHub git smart-http 经常卡死（HTTPS 能 200 但 clone 走不完）；`DEMO_GIT_REMOTE` 留空、走 rsync + 本地 git init 更稳。
