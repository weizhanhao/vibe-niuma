# Plan 5 — 端到端整合 + 真实 E2E 冒烟 + ECS 部署

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Plan 1-4 的产物（demo 应用、Orchestrator + 真实 adapter、浏览器扩展）在一台阿里云 ECS 上完整部署并打通整条闭环：业务员在 demo 页面框选 → 扩展捕获 → Orchestrator 驱动真实 dev runner 改代码 → Docker 预览 → 确认合并。交付一次可复现的真实 E2E 冒烟。

**Architecture:** 所有组件跑在一台 ECS（设计文档 §3）：demo git 仓库、Orchestrator（FastAPI，systemd 常驻）、Anthropic-compatible 代理（claude-code-router / LiteLLM，systemd 常驻）、MySQL（Orchestrator 库 + demo 库）、每变更请求一个 Docker 预览容器。浏览器扩展在用户本地 Chrome，通过公网访问 ECS 上的 Orchestrator。部署用一套幂等的 provisioning 脚本 + systemd unit + docker-compose（基础设施层），不引入 k8s（设计文档 §8：不做生产部署/加固）。

**Tech Stack:** Bash provisioning 脚本 + systemd units + docker / docker-compose；ECS（阿里云，国内网络）；MySQL 8；Python venv（Orchestrator）；Node（demo 前端构建 + 扩展构建）；Anthropic-compatible 代理（claude-code-router 或 LiteLLM）；`ssh` / `rsync` 做部署投递。

---

## 前置约定（每个任务都假定已满足）

- Plan 1-4 已全部完成并合并到 `main`。本计划在新分支 `plan5-e2e-integration-ecs-deploy` 上做。
- 用户已提供 ECS 访问方式（公网 IP/域名、SSH 用户 + 密钥）、规格（CPU/内存/磁盘 → 并发容器上限）、OS 版本、预装情况。
- 用户已提供模型与代理选择 + API key（dev runner 模型、vision 模型、Anthropic-compatible 代理类型）。
- 用户已确认端口范围、域名 + HTTPS 还是 IP + 自签、代码托管（是否推 GitHub/Gitee）。
- 安全不考虑（设计文档 §2、§8）：不做认证、不做容器加固。
- 部署脚本必须**幂等**：重复跑不破坏已有状态。
- 所有密钥经环境变量 / `.env` 注入，不进 git（沿用项目 `.gitignore` 约定）。

## File Structure

```
deploy/
  README.md                    # 部署总说明：从零到跑通的步骤
  provision.sh                 # ECS 初始化：装 docker/python/node/git/mysql-client（幂等）
  deploy.sh                    # 投递 + 重启：rsync 代码、装依赖、重启 systemd 服务
  env.example                  # 部署所需全部环境变量样板
  systemd/
    vibe-niuma-orchestrator.service   # Orchestrator 常驻
    vibe-niuma-llm-proxy.service      # Anthropic-compatible 代理常驻
  mysql/
    docker-compose.yml         # MySQL 8 容器（Orchestrator 库 + demo 库）
    init.sql                   # 建库 + demo 后端表结构
  llm-proxy/
    config.example.yml         # 代理配置样板（claude-code-router 或 LiteLLM）
  healthcheck.sh               # 部署后一键健康检查（各组件 + 端到端）
orchestrator/src/orchestrator/config.py   # 修改：生产环境配置项（如 preview_host）
orchestrator/tests/test_e2e_smoke.py      # 修改/启用：Plan 3 留的冒烟测试，本计划真跑
docs/RUNBOOK.md                # 运维手册：怎么看日志、重启、排障、清理预览容器
README.md                      # 项目根 README（总览）
```

---

## Task 1: 部署环境变量与配置梳理

**Files:**
- Create: `deploy/env.example`
- Modify: `orchestrator/src/orchestrator/config.py`（如缺生产项）
- Modify: `orchestrator/.env.example`
- Test: `orchestrator/tests/test_config.py`（如加配置项则追加用例）

- [ ] **Step 1** — 盘点 Plan 2/3 已有配置项 + ECS 部署新增项，整理成 `deploy/env.example`：ECS 侧的 `DEMO_REPO_PATH`、`DATABASE_URL`（指向 ECS MySQL）、`PREVIEW_HOST`（ECS 公网 IP/域名，拼预览 URL 用）、`ORCHESTRATOR_PORT`、`PREVIEW_PORT_MIN/MAX`、`QUOTA_SIZE`（按 ECS 规格定）、`DEV_RUNNER`、`DEV_MODEL`、`ANTHROPIC_BASE_URL`（指向本机代理）、`LLM_API_KEY`、`VISION_MODEL`、`IDLE_TTL_SECONDS`、`REAPER_INTERVAL_SECONDS` 等。
- [ ] **Step 2** — 若 `config.py` 缺 `preview_host`（Plan 3 的 `DockerPreviewAdapter` 拼预览 URL 时本地用 `localhost`，ECS 上要用公网地址）—— 加 `preview_host` 配置项 + 失败测试 + 实现 + 通过；并让 `DockerPreviewAdapter` 用它拼 URL。
- [ ] **Step 3** — 同步更新 `orchestrator/.env.example`。
- [ ] **Step 4: 提交** — `git commit -m "feat: 部署环境变量梳理与 preview_host 配置项"`

---

## Task 2: MySQL 部署单元

**Files:**
- Create: `deploy/mysql/docker-compose.yml`, `deploy/mysql/init.sql`

- [ ] **Step 1** — `docker-compose.yml`：MySQL 8 容器，数据卷持久化，端口按约定（ECS 上可用 3306；本地开发仍 3307 避让）。环境变量从 `.env` 注入 root 密码。
- [ ] **Step 2** — `init.sql`：建 `orchestrator` 库、`demo` 库；建 demo 后端表结构（从 Plan 1 的 demo 后端模型定义取，保持一致 —— 先读 `demo/backend/` 再写）。
- [ ] **Step 3** — 本地起一遍 `docker compose up -d` 验证建库成功（用本地避让端口），再 `down`。
- [ ] **Step 4: 提交** — `git commit -m "feat: MySQL 部署单元（compose + 建库脚本）"`

---

## Task 3: Anthropic-compatible 代理部署单元

**Files:**
- Create: `deploy/llm-proxy/config.example.yml`
- Create: `deploy/systemd/vibe-niuma-llm-proxy.service`

- [ ] **Step 1** — 按用户选定的代理（claude-code-router / LiteLLM / one-api）写 `config.example.yml`：把 Anthropic 协议的请求转发到用户指定的国内模型（DeepSeek / 通义千问）。配置里 key 用占位符，真值由 `.env` 注入。
- [ ] **Step 2** — `vibe-niuma-llm-proxy.service`：systemd unit，常驻、开机自启、崩溃重启；监听 `ANTHROPIC_BASE_URL` 对应端口。
- [ ] **Step 3** — 校验 unit 文件语法（`systemd-analyze verify` 若可用）+ 代理配置语法。
- [ ] **Step 4: 提交** — `git commit -m "feat: Anthropic-compatible 代理部署单元"`

> **给执行者（决策点）：** 代理类型由用户在前置清单里给定。若用户未明确，向人确认 —— 这是影响 dev runner 能否工作的关键依赖。

---

## Task 4: Orchestrator 部署单元

**Files:**
- Create: `deploy/systemd/vibe-niuma-orchestrator.service`

- [ ] **Step 1** — `vibe-niuma-orchestrator.service`：systemd unit，`ExecStart` 跑 `venv/bin/uvicorn orchestrator.main:app --host 0.0.0.0 --port ${ORCHESTRATOR_PORT}`，`EnvironmentFile` 指向部署的 `.env`，依赖 MySQL 与 llm-proxy（`After=`/`Wants=`），崩溃重启、开机自启。
- [ ] **Step 2** — 校验 unit 语法。
- [ ] **Step 3: 提交** — `git commit -m "feat: Orchestrator 部署单元（systemd）"`

---

## Task 5: provision.sh — ECS 初始化

**Files:**
- Create: `deploy/provision.sh`

- [ ] **Step 1** — `provision.sh`（幂等）：在 ECS 上安装/确认 docker + docker compose plugin、python3 + venv、node + npm、git、mysql-client；配置 npm/pip 国内镜像（npmmirror / 清华 pypi）；创建部署目录结构；已装的跳过。每步可重入。
- [ ] **Step 2** — 脚本用 `set -euo pipefail`；大量 `command -v X || install X` 形式；每步打印清晰进度。
- [ ] **Step 3** — `bash -n deploy/provision.sh` 语法检查；在 ECS（或等价环境）干跑验证可重入性。
- [ ] **Step 4: 提交** — `git commit -m "feat: provision.sh ECS 初始化脚本"`

> **给执行者：** 用户会在前置清单给出 ECS 的 OS 版本与预装情况 —— 按实际裁剪安装步骤。

---

## Task 6: deploy.sh — 投递与重启

**Files:**
- Create: `deploy/deploy.sh`

- [ ] **Step 1** — `deploy.sh`（幂等）：① `rsync` 把 `orchestrator/`、`demo/`、`deploy/` 投到 ECS（排除 `venv/`、`node_modules/`、`__pycache__/`、`.git/`）；② 在 ECS 上：建/更新 Orchestrator venv 并 `pip install -e ".[dev]"`、构建 demo 前端、clone/更新 demo 仓库到 `DEMO_REPO_PATH` 并确保有 `main` 分支；③ 安装/刷新 systemd units、`systemctl daemon-reload`、重启 `vibe-niuma-orchestrator` + `vibe-niuma-llm-proxy`；④ 起/确认 MySQL compose。
- [ ] **Step 2** — 支持参数：`--code-only`（只投代码重启服务）、`--full`（含 provision）。
- [ ] **Step 3** — `bash -n` 语法检查。
- [ ] **Step 4: 提交** — `git commit -m "feat: deploy.sh 投递与重启脚本"`

> **给执行者（决策点）：** demo 仓库是否推到远端（GitHub/Gitee）由用户在前置清单决定。若推远端，`deploy.sh` 在 ECS 上 `git clone`；若不推，`rsync` demo 并在 ECS 上 `git init` + 初始 commit。按用户答复实现其一。

---

## Task 7: healthcheck.sh — 部署后健康检查

**Files:**
- Create: `deploy/healthcheck.sh`

- [ ] **Step 1** — `healthcheck.sh`：依次检查 ① MySQL 可连 ② llm-proxy 端口响应 ③ Orchestrator `GET /health` 返回 ok ④ demo 仓库存在且有 `main` ⑤ docker daemon 可用 ⑥ 配额端口区间未被占满。每项打印 PASS/FAIL，任一 FAIL 整体非 0 退出。
- [ ] **Step 2** — `bash -n` 语法检查。
- [ ] **Step 3: 提交** — `git commit -m "feat: healthcheck.sh 部署后健康检查"`

---

## Task 8: 真实 E2E 冒烟（在 ECS 上真跑）

**Files:**
- Modify: `orchestrator/tests/test_e2e_smoke.py`（Plan 3 留的骨架）

- [ ] **Step 1** — 完善 Plan 3 的 `test_e2e_smoke.py`：使其能用部署的 `.env`（真实 ECS 配置）跑 —— 对 ECS 上的 demo 仓库，走真实 `BrainstormingSkill`（脚本化回答澄清）+ 真实 dev runner（经代理打真实模型）+ 真实 build + 真实 Docker preview，断言：到 `preview-ready`、git diff 非空、预览 URL（用 `PREVIEW_HOST`）可达；再调 `/merge`，断言 `merged` 且改动进了 demo 仓库 `main`。
- [ ] **Step 2** — 在 ECS 上 `VIBE_NIUMA_E2E=1 venv/bin/pytest tests/test_e2e_smoke.py -v -s` 真跑一次，跑通。若 dev runner 产出不稳定，记录观察、必要时调 prompt（属于 Plan 3 的 adapter，跨计划改动需在 commit 说明）。
- [ ] **Step 3: 提交** — `git commit -m "test: 真实 E2E 冒烟在 ECS 上跑通"`

> **给执行者：** 这是设计文档 §9 三大风险假设的真实验证点 —— URL→路由映射、dev runner 凭 brief+截图产出、Docker 预览启动延迟。跑的过程中如发现某个假设不成立，**立即向人汇报**，不要自行大改架构。

---

## Task 9: 扩展连真实 ECS 联调

**Files:**
- Modify: `extension/README.md`
- Create: `docs/RUNBOOK.md`

- [ ] **Step 1** — 把扩展的 Orchestrator 地址指向 ECS 公网地址，在真实 Chrome 里加载扩展，对着 ECS 上的 demo 站点手动走一遍完整闭环：框选 → 输入需求 → 澄清 → 看状态 → 开预览 → 确认合并。记录每一步的实际表现与延迟。
- [ ] **Step 2** — 把联调中发现的问题分类：① 扩展 bug → 本任务内最小修复；② Orchestrator/adapter bug → 记录并向人汇报，按严重度决定是否本计划内修。
- [ ] **Step 3** — 写 `docs/RUNBOOK.md`：怎么看各组件日志（`journalctl -u vibe-niuma-orchestrator` 等）、怎么重启、怎么清理残留预览容器、常见故障排查、改配置后怎么生效。更新 `extension/README.md` 的「连 ECS」说明。
- [ ] **Step 4: 提交** — `git commit -m "docs: ECS 联调结果、RUNBOOK 与扩展连线说明"`

---

## Task 10: 部署总文档 + 收尾

**Files:**
- Create: `deploy/README.md`
- Create: 项目根 `README.md`

- [ ] **Step 1** — `deploy/README.md`：从零到跑通的完整步骤 —— 填 `env.example` → `provision.sh` → 起 MySQL → 配 llm-proxy → `deploy.sh --full` → `healthcheck.sh` → 加载扩展 → 跑一遍闭环。含每步预期输出与排障指引。
- [ ] **Step 2** — 创建项目根 `README.md`：项目总览（指向设计文档）、5 个 plan 的产物、目录结构（`demo/` `orchestrator/` `extension/` `deploy/` `docs/`）、快速上手入口。
- [ ] **Step 3** — 全量回归：`orchestrator` 全测试（含契约，排除 `-m e2e`）+ `extension` 全测试 + `demo` 既有测试，全绿。
- [ ] **Step 4: 提交** — `git commit -m "docs: 部署总文档与项目 README"`

---

## 验收标准（Plan 5 完成定义 = MVP 完成定义）

- [ ] `deploy/` 下脚本幂等可重入：`provision.sh` + `deploy.sh --full` + `healthcheck.sh` 在 ECS 上跑通。
- [ ] ECS 上五件套全部常驻可用：demo 仓库、Orchestrator、llm-proxy、MySQL、Docker 预览能力。
- [ ] 真实 E2E 冒烟（`VIBE_NIUMA_E2E=1`）在 ECS 上跑通：一条已知改动从 `created` 到 `merged`，改动真进了 demo `main`。
- [ ] 浏览器扩展连真实 ECS，手动走通完整闭环（框选 → 澄清 → 预览 → 合并）。
- [ ] `deploy/README.md` + `docs/RUNBOOK.md` 完整，他人可照着复现部署。
- [ ] 设计文档 §9 的三大风险假设有真实验证结论（写进 RUNBOOK 或联调记录）。
- [ ] 所有计划已提交，`git status` 干净，全量测试绿。

---

## 需要用户提供（运行 Plan 5 前的一次性清单 —— 这是最关键的一份）

1. **ECS 访问**：公网 IP 或域名；SSH 登录用户 + 私钥（或密码）；sudo 权限确认。
2. **ECS 规格**：CPU / 内存 / 磁盘 —— 用来定 `QUOTA_SIZE`（并发预览容器上限）。
3. **ECS 环境**：OS 版本（如 Alibaba Cloud Linux 3 / Ubuntu 22.04）；已预装哪些（Docker / Python / Node / git / MySQL）。
4. **端口**：ECS 上可对外开放的端口范围 —— Orchestrator 端口、预览容器端口区间（`PREVIEW_PORT_MIN/MAX`）、llm-proxy 端口、MySQL 端口。
5. **域名与 HTTPS**：用域名 + HTTPS 证书，还是 IP + 自签（MVP 可接受 IP + 纯 HTTP，安全不考虑）。决定扩展里填的 Orchestrator 地址形态。
6. **模型与代理**：dev runner 用 claude-code 还是 opencode；`dev_model`（DeepSeek / 通义千问 / 其它）+ key；Anthropic-compatible 代理选 claude-code-router / LiteLLM / one-api；vision 模型 + key。
7. **代码托管**：demo 仓库是否推到 GitHub/Gitee（影响 `deploy.sh` 是 `git clone` 还是 `rsync` + 本地 `git init`）。vibe-niuma 项目本身是否也推远端。
8. **网络可达性**：ECS 能否访问 npm / pip / DockerHub，还是必须走国内镜像（影响 `provision.sh` 镜像配置）。
9. **demo 内容确认**：「订单管理 mini 应用」+ 干净内部工具风格 —— 沿用 Plan 1 的实现，是否还要调整。
