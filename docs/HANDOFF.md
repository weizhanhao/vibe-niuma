# 交接 · 一次性输入清单

Plans 1–5 已全部开发完。下面是**你需要一次性提供的全部输入**，给了之后整条闭环就能跑起来。所有键名跟 `deploy/env.example` / `orchestrator/.env.example` 对得上。

## 已经做完（不需要你再决定）

- ✅ Plan 1：demo 应用（订单管理 mini app），已合并 main。
- ✅ Plan 2：Orchestrator 骨架（FSM/REST/SSE/git/配额/reaper），79 测试通过。
- ✅ Plan 3：4 个真实 adapter（ReactVite / Docker / Claude+OpenCode / Brainstorming），契约测试通过。
- ✅ Plan 4：MV3 Chrome 扩展（7 panel + REST/SSE），24/24 测试通过、`npm run build` 绿。
- ✅ Plan 5：部署脚本（provision/deploy/healthcheck）+ systemd + LiteLLM proxy + RUNBOOK。

`git log main..HEAD` 在 plan5 分支上有 33 个 commit；分支按计划串行叠（plan2 → plan3 → plan4 → plan5）。

## 你需要提供（一次性）

### A. ECS 接入

| 键 | 含义 | 例子 |
|---|---|---|
| `ECS_HOST` | 公网 IP 或域名 | `47.x.y.z` 或 `vibe-niuma.example.com` |
| `ECS_USER` | SSH 登录用户 | `root` / `ecs-user` |
| `ECS_SSH_KEY` | 本机私钥路径 | `~/.ssh/id_ed25519` |
| `DEPLOY_ROOT` | ECS 部署根目录 | `/opt/vibe-niuma`（默认） |
| OS 版本 + 预装情况 | 影响 `provision.sh` 跳过哪些 | `Alibaba Cloud Linux 3` / `Ubuntu 22.04` |
| sudo 权限 | provision 需要装 docker / node / python | 确认 |

### B. ECS 规格 → 配额

| 键 | 含义 | 例子 |
|---|---|---|
| `QUOTA_SIZE` | 并发预览容器上限 | 内存 ≥ 4G 给 5；≥ 8G 给 10 |
| `PREVIEW_PORT_MIN` / `_MAX` | 预览容器端口区间 | `5100-5199` |

### C. 模型 + 代理

| 键 | 含义 | 例子 |
|---|---|---|
| `DEV_RUNNER` | dev runner 工具 | `claude-code`（默认）/ `opencode` |
| `DEV_MODEL` | dev runner 用的模型 | `deepseek-chat` / `qwen-max` |
| `ANTHROPIC_BASE_URL` | LiteLLM 代理地址 | `http://127.0.0.1:8787`（默认） |
| `LLM_API_KEY` | dev runner + 澄清共用的 key | DeepSeek/通义/openai key |
| `VISION_MODEL` | 澄清看截图的视觉模型 | `qwen-vl-plus` |
| **`deploy/llm-proxy/config.yml`** | LiteLLM 真实 provider 配置 | 拷贝 `config.example.yml` 后填 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / ANTHROPIC_API_KEY |

### D. 端口

| 键 | 含义 | 例子 |
|---|---|---|
| `ORCHESTRATOR_PORT` | Orchestrator 对外端口 | `9000` |
| `LLM_PROXY_PORT` | LiteLLM 端口 | `8787` |
| `MYSQL_PORT` | MySQL 暴露端口 | `3306` |
| `PREVIEW_HOST` | 拼预览 URL 用的主机名 | 就是 `ECS_HOST`，扩展点开预览要靠它 |

ECS 安全组需要放行：`ORCHESTRATOR_PORT` + `PREVIEW_PORT_MIN..PREVIEW_PORT_MAX`。

### E. 域名 / HTTPS（可选）

MVP 不强求；不填就用 `http://<ECS_IP>:9000`，扩展 Settings 里填这个。
若要 HTTPS，自己挂 nginx 反代（本计划没含，设计 §2/§8 OUT）。

### F. 代码托管

| 键 | 含义 | 取值 |
|---|---|---|
| `DEMO_GIT_REMOTE` | demo 仓库 git 远端 | 空 = rsync 投递 + ECS 上 `git init`（推荐 MVP）；填 URL = ECS 上 `git clone` |

vibe-niuma 项目本身（这个 repo）是否推 GitHub/Gitee 由你定，**对部署本身没影响**（deploy.sh 是从本机 rsync）。

### G. 网络可达性

| 键 | 含义 |
|---|---|
| `USE_NPM_MIRROR=1` | npm 走 npmmirror（默认开） |
| `USE_PIP_MIRROR=1` | pip 走清华（默认开） |
| 大模型 API 出网 | ECS 必须能访问 DeepSeek / 通义 / Anthropic 等真实 provider 的 API 域名 |

### H. 本机环境（执行 deploy.sh 的那台）

- `ssh` / `rsync` 可用（macOS 默认有）。
- 浏览器扩展开发不强依赖；想本地跑 `cd extension && npm install && npm run build` 也行。

---

## 收到上面 A–H 之后，我会一次性做：

1. `cp deploy/env.example deploy/.env` → 把你给的值填进去。
2. `cp deploy/llm-proxy/config.example.yml deploy/llm-proxy/config.yml` → 把模型 key 填进去（或单独让你把 key 放到 ECS 的环境变量，更安全）。
3. `bash deploy/deploy.sh --full` → 第一次完整部署。
4. `bash deploy/healthcheck.sh` → 体检全绿。
5. `ssh ... "cd /opt/vibe-niuma/orchestrator && VIBE_NIUMA_E2E=1 venv/bin/pytest -m e2e -v -s"` → 真实闭环冒烟。
6. 把扩展 `dist/` 给你加载到 Chrome，对着 ECS demo 站走一遍框选 → 澄清 → 预览 → 合并。
7. 把验证结果回填到 `docs/RUNBOOK.md` 的「风险假设的真实验证」一节。

## 你**不**需要做的

- 写代码 / 测试 / 部署脚本（都做完了）。
- 决定架构（设计文档 + 5 份 plan 都已经过审）。
- 配 MySQL / Docker / Python / Node（provision.sh 全自动）。

## 分支状态

```
main                                    Plan 1 + Plan 2 计划文档
└─ plan2-orchestrator-skeleton          Plan 2 实现（79 测试）
   └─ plan3-adapter-implementations     Plan 3 实现（contract + docker 共 136 测试）
      └─ plan4-browser-extension        Plan 4 实现（24 测试）
         └─ plan5-e2e-integration-...   Plan 5 部署脚本（你在这里）
```

提供完输入后，先把 plan2..plan5 依次合并 main（或直接把 plan5 fast-forward 进 main），再跑 deploy.sh。
