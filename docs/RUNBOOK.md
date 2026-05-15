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

## Plan 6 自助配置后的运维

Plan 6 把模型 / API key / 项目路径等配置从 `/opt/doskill/.env`（部署机改 + ssh + 重启）搬到了
`system_config` 表 + 扩展端引导向导。日常改配置不再需要 ssh ECS。

### admin.token —— 扩展与 admin API 之间的握手

`/opt/doskill/admin.token` 是 32 字节随机串，由 `doskill-orchestrator.service` 的 `ExecStartPre`
首次启动时生成，权限 `0600`，属主 root。

- **谁能读**：ECS 上能 ssh 进去的人（运维 + 部署机持有人）
- **怎么拿**：`ssh root@ECS 'cat /opt/doskill/admin.token'`，粘到扩展引导向导 Step 2
- **丢了 / 想换**：

  ```bash
  ssh root@ECS '
    rm -f /opt/doskill/admin.token
    systemctl restart doskill-orchestrator
    sleep 2
    cat /opt/doskill/admin.token
  '
  ```

  老 token 立即作废；所有装着老 token 的扩展下次 PUT 会 401。把新 token 粘回扩展即可恢复。

### system_config 表手工查看 / 调试

```bash
ssh root@ECS "docker exec doskill-mysql mysql -uroot -pdemopass \
  -e 'USE orchestrator; SELECT * FROM system_config\\G'"
```

期望就一行（singleton id=1），含 `dev_runner / dev_model / vision_model /
deepseek_api_key / dashscope_api_key / anthropic_api_key / demo_repo_path /
preview_backend_url / version / updated_at`。

### 改了哪些字段会触发什么

| 改的字段 | 期望发生 | 怎么验证 |
|---|---|---|
| `deepseek_api_key` / `dashscope_api_key` / `anthropic_api_key` | `doskill-llm-proxy.service` 自动重启（PUT 响应里 `restartedServices` 含 `litellm`） | `systemctl status doskill-llm-proxy \| head` 看 `Active:` 时间戳变了 |
| `dev_runner` / `dev_model` / `vision_model` | 不重启 orchestrator，下次 CR 立即读新值（`settings.cache_clear()`） | 发一条新 CR，orchestrator 日志里走的 runner / model 是新的 |
| `demo_repo_path` / `preview_backend_url` | 同上，不重启 | 同上 |

### 扩展端配置位置

- chrome.storage.local 的键：`doskill_config_v2`（zod schema 在 `extension/src/lib/config.ts`）
- 字段：`orchestratorUrl + adminToken + configVersion + server.{...}`
- API key 字段 **不存** chrome.storage，只在 PUT body 里传一次给 orchestrator

### 重置扩展全部配置（业务员侧）

`chrome://extensions/` → doskill 详情 → 「网站数据」/「清除存储」→ 重新装一遍引导向导。或在
扩展 service worker 的 DevTools console：

```js
chrome.storage.local.remove('doskill_config_v2')
```

### 排障：扩展端 401 一直转圈

可能原因：

1. admin.token 被换过（systemctl restart 后老 token 失效）→ 重新 ssh 拿 + 粘进扩展
2. 扩展端 token 输错（zod schema 校验长度 ≥ 32）→ 引导向导 Step 2 自带正则检查
3. orchestrator 启动失败 `/admin.token` 没生成 → `journalctl -u doskill-orchestrator -n 50`

## 风险假设的真实验证（设计文档 §9）

2026-05-15 首次真实 E2E 闭环跑通（114.55.171.64 / Alibaba Cloud Linux 4 / opencode + deepseek/deepseek-v4-flash），结论：

- [x] **URL→路由源文件映射** —— `/settings` 命中 `frontend/src/pages/Settings.tsx`，正则 + import 解析 OK。`/orders/:id` 动态段还没真正打过（建议下一轮补一次）。
- [x] **dev runner 凭 brief 产出可用改动** —— "把保存按钮改成立即保存" 一次 commit、零返工。改动落在 `cr/<id>` 分支后被自动合并到 main。
- [x] **Docker 预览启动延迟** —— 全闭环（clarifying → coding → building → preview-ready → merged）耗时 **2 分 43 秒**，预览容器 build + healthy 约 60–80s，业务员等到的「按钮变了」典型 < 3 分钟。可忍。

补充教训（不在设计预期内但跑下来发现的）：
- 用 claude-code CLI 时跟 **DeepSeek hybrid thinking mode** 协议不兼容（要求把 `reasoning_content` 回传，claude-code 不带）。默认改用 **opencode**；想用 claude-code 就走真 Claude key + Claude 模型，别走 DeepSeek。
- 国内 ECS 上 GitHub git smart-http 经常卡死（HTTPS 能 200 但 clone 走不完）；`DEMO_GIT_REMOTE` 留空、走 rsync + 本地 git init 更稳。
