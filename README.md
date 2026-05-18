# 🐂 vibe-niuma · doskill

> **业务员在浏览器里画个框 + 说人话，AI 直接改你产品的代码、起预览给他看、一键合并回 main。**
>
> 程序员只维护「可编辑表面」与平台本身，不再逐条处理业务员的小改动。
>
> 仓库代号 `vibe-niuma`（业务时代的牛马），产品代号 `doskill`。

```
[业务员的 Chrome]                            [ECS 上的 vibe-niuma]
                                          ┌──────────────────────────┐
  📍 框一块区域 + 「订单徽章改红色」  ───►│ Orchestrator (FastAPI)   │
                                          │   ① 截图 + URL 进澄清    │
                                          │   ② AI 看图判业务意图    │──► DeepSeek
                                          │   ③ opencode 真改代码    │──► Qwen-VL
                                          │   ④ vite build + docker  │
                                          │   ⑤ 起隔离预览容器       │
                                          └──────────────────────────┘
                                                       │
              👀 看预览满意 ──► 「确认合并」 ──► main 自动重建 :5199
```

---

## 目录

- [这是什么](#这是什么)
- [一个真实例子](#一个真实例子30-秒看完)
- [核心特性](#核心特性)
- [架构](#架构)
- [快速开始](#快速开始)
- [使用流程 E2E](#使用流程-e2e)
- [配置](#配置)
- [项目结构](#项目结构)
- [开发](#开发)
- [相关文档](#相关文档)

---

## 这是什么

传统低代码的核心假设是「拖拽组件比写代码快」。在 AI 时代，瓶颈不再是 UI 拼装，而是**业务意图的表达**。

vibe-niuma 把这条链路完整接起来：

1. **捕获**：业务员在他每天用的产品页面上框选一块区域 + 用自然语言说需求。
2. **理解**：AI 看截图判断业务意图，不确定就问澄清问题。
3. **改代码**：在隔离分支上让 AI dev runner（opencode / claude-code）真的改源码 + commit。
4. **预览**：每条变更起一个独立 Docker 容器，业务员在浏览器看效果。
5. **合并**：业务员点「确认合并」→ rebase 回 main → main demo 站自动重建。

整个 MVP 跑在一台阿里云 ECS 上，没有任务队列、没有 K8s、没有微服务 —— 一个 FastAPI 单体 + 4 个干净的 Adapter 接口包住「与栈相关」的所有代码。

---

## 一个真实例子（30 秒看完）

业务员小王打开公司订单管理后台 `:5199/orders`，发现「未发货」徽章颜色不够醒目。

1. 点扩展图标 → 📍 框选那个徽章 → 输入「**未发货状态徽章改成红色 + 字号大一点**」→ 提交。
2. 侧边栏开始滚 SSE 日志：
   ```
   ▸ 澄清意图...   AI: "未发货徽章改红色，字号加大。是否同时改 hover 态？"
   ◀ 业务员答：     "✓ 够了直接干"
   ▸ 定位到入口源文件：demo/frontend/src/pages/Orders.tsx
   ▸ opencode 改代码...
     - frontend/src/pages/Orders.tsx
     + className="badge badge-danger text-lg"
   ▸ vite build... ✓ 2.1s
   ▸ 起预览容器 :5147... ✓
   ✅ 预览就绪 →  http://<ECS>:5147/orders
   ```
3. 点「↗ 新标签打开」看预览：徽章已经变成红色 + 加大字号。
4. 满意 → 点「确认合并 →」→ 几秒后 `:5199/orders` 自动刷新，新 UI 上线。

整个过程小王没碰 git、没碰 IDE、没找开发同事；程序员事后 `git log` 能看到这条 commit 是谁、改了什么、为什么改。

---

## 核心特性

- 🎯 **框选 + 自然语言** —— 不会写代码也不影响表达，截图自带坐标，AI 一眼定位入口源文件
- 💬 **真多轮澄清** —— 不限轮数，AI 觉得没问清就继续问，业务员一句「✓ 够了直接干」立即中断
- 🧠 **意图自动路由** —— 一条消息 LLM 判断是「新需求 / 续改 / 闲聊」自动分流（new_cr / refine_cr / chat_only）
- 🌱 **隔离分支 + 隔离容器** —— 每条变更一个 `cr/<id>` 分支 + 一个独立 Docker 预览，互不污染
- 🔄 **refine 续改秒级反馈** —— 「字号再大一点」复用同分支同容器，Vite 热重载
- 🛟 **失败自愈** —— LLM 决定 retry / retry_with_revised_prompt / escalate，最多 2 次自动恢复 transient 错
- 📸 **多附件 vision** —— 一条 message 可附 0-3 张图（框选 / 粘贴 / 整页 / 文件），多图一次塞 vision API
- 🗂️ **多项目** —— 扩展可在多个独立产品间切换，每项目自带 orchestrator 地址 / token / 模型配置
- 🎙️ **Cursor 式持续对话** —— conversation 持久化 + 动态压缩（超 40k tokens 自动 summarize 老消息）
- 🎩 **AI 引导部署** —— 装好扩展只填一个 DeepSeek API key，剩下「服务器买哪、命令怎么跑」由 AI 用对话引导
- 🔌 **4 个 Adapter 接口** —— 换栈 / 换 dev runner / 换预览方式只动一个文件，主体逻辑不变

---

## 架构

```
┌─────────────────────┐         ┌──────────────────── 阿里云 ECS ────────────────────┐
│  浏览器扩展 (MV3)    │  HTTPS  │  ┌──────────────────────────────────────────┐      │
│  · 框选 + 截图       │ ──────► │  │  Orchestrator (FastAPI 单体)              │      │
│  · 输入需求          │  REST   │  │   REST API + SSE + 状态机 + 任务编排     │      │
│  · 澄清问答          │ ◄────── │  │  ┌────────────┐┌─────────┐┌──────────┐  │      │
│  · 实时状态 (SSE)    │  SSE    │  │  │Interaction ││ Stack   ││ Preview  │  │      │
│  · 看预览 / 合并     │         │  │  │   Skill    ││ Adapter ││  Adapter │  │      │
└─────────────────────┘         │  │  └────────────┘└─────────┘└──────────┘  │      │
                                │  │  ┌────────────────────────┐              │      │
                                │  │  │   DevRunner Adapter    │              │      │
                                │  │  └────────────────────────┘              │      │
                                │  └──────────────────────────────────────────┘      │
                                │       │           │              │           │     │
                                │       ▼           ▼              ▼           ▼     │
                                │  ┌─────────┐┌─────────┐┌─────────────┐┌─────────┐  │
                                │  │demo repo││分支cr/id││预览容器集合 ││ MySQL   │  │
                                │  └─────────┘└─────────┘└─────────────┘└─────────┘  │
                                └────────────────────────────────────────────────────┘
```

### 4 个 Adapter

| Adapter | 干什么 | 当前实现 |
|---|---|---|
| `InteractionSkill` | 看截图 + 项目知识，判断业务意图、出澄清问题 | `BrainstormingSkill` + Qwen-VL streaming |
| `StackAdapter` | URL → 源文件，打包代码上下文，跑构建 | `ReactViteStackAdapter`（Vite build） |
| `DevRunnerAdapter` | 业务 brief → 真改代码 + commit | `OpenCodeDevRunner`（opencode CLI + DeepSeek） |
| `PreviewAdapter` | 分支 → 隔离预览容器 | `DockerPreviewAdapter`（vite dev :5xxx） |

`interfaces.py` 是契约，主体逻辑只依赖 Protocol。换栈 / 换 dev runner / 换预览方式只动 `adapters/impl/` 对应文件。

### 状态机

```
created → clarifying → located → coding → building → preview-ready ─┬─► merged
                          ↑          │                              ├─► discarded
                          └──────────┘ (refine)                     └─► failed
```

- `preview-ready` 是**非终态**（仍占配额），由 merge / discard / TTL 释放。
- 每个 phase 失败都进 `failed`，self_heal 会决定要不要 retry。
- SSE 事件类型：`status` / `question` / `variants` / `log` / `question-resolved`。

### 模型 / 数据流

- **澄清模型 + 视觉**：`qwen-vl-plus`（DashScope，看截图判业务意图）
- **dev runner 模型**：`deepseek-v4-pro`（**当前 ECS 实际跑的**，代码能力强）；`deepseek-v4-flash`（出厂 `env.example` 默认，便宜快但偶尔需要 self-heal 重试）
- **聊天 / refine 文本**：同上 dev_model，走 LiteLLM
- **切换**：改 `deploy/.env` 的 `DEV_MODEL`（`deepseek/deepseek-v4-pro` 或 `deepseek/deepseek-v4-flash`）→ `systemctl restart doskill-orchestrator`。LiteLLM 路由表（`deploy/llm-proxy/config.yml`）里两个都 wire 上了，无需改 proxy 配置。
- **数据库**：MySQL 8（orchestrator 表 + demo 业务表，各自独立 schema）
- **预览**：每个 CR 一个 docker 容器，端口段 `5100-5199`，TTL 30min 闲置回收

---

## 快速开始

按你身份选一条路径。下面 3 条都能跑通，区别在「谁来管服务器」。

### 路径 A · 业务员一键装（推荐）

业务员自己只点扩展、填 DeepSeek API key、跟着助手对话答几个问题；剩下的 AI 引导他完成。

1. **业务员**：[下载扩展](#加载-chrome-扩展) → 装好 → 填 `sk-...`（DeepSeek API key）。
2. **AI 助手**接管对话，问业务员：「你要本地试试 还是 阿里云 ECS？」
3. 业务员选**本地**：助手让他贴 `docker --version` 输出 → 引导跑 `bash deploy/local.sh`。
4. 业务员选 **ECS**：助手让他贴 ECS 公网 IP + SSH 私钥（**只存浏览器内存，关浏览器即清**），帮他拼一条 `ssh root@<IP> 'curl -fsSL https://raw.githubusercontent.com/weizhanhao/vibe-niuma/main/deploy/ecs-bootstrap.sh | sudo bash -s -- --deepseek-key sk-XXXX'` 让他在终端跑。
5. 助手等部署完，自动把 orchestrator URL + admin token 填进扩展，验证健康检查 11/11 通过 → 业务员进框选主流程。

整个过程业务员不需要懂 docker / git / ssh，只需要在终端复制粘贴命令、把命令输出贴回助手。

### 路径 B · 开发者本地 Docker（试一下）

```bash
# 1. clone
git clone https://github.com/weizhanhao/vibe-niuma.git doskill && cd doskill

# 2. 准备 .env
cp deploy/env.example deploy/.env
# 编辑 deploy/.env，至少填 LLM_API_KEY=sk-deepseek...
#                       ECS_HOST=127.0.0.1
#                       PREVIEW_HOST=localhost

# 3. 跑本地部署
bash deploy/local.sh    # 起 mysql + orchestrator + llm-proxy + main demo

# 4. 验证
bash deploy/healthcheck.sh   # 期望「通过 11 · 失败 0」

# 5. 拿 admin token
cat ~/doskill/admin.token

# 6. 扩展 Setup Wizard 填 http://localhost:9000 + token
```

### 路径 C · 自己运维 ECS（生产推荐）

ECS 至少 4C / 8 GiB，安全组放行 `22 / 9000 / 5100-5199`（SSH + orchestrator + 预览端口段；LLM proxy 8787 和 demo backend 8000 都只在本机/容器网络，不对外）。预装 git 即可。

**一条命令搞定**（在 ECS 上跑，不在本机）：

```bash
curl -fsSL https://raw.githubusercontent.com/weizhanhao/vibe-niuma/main/deploy/ecs-bootstrap.sh \
  | sudo bash -s -- \
      --deepseek-key sk-deepseekXXXXXXXX \
      [--dashscope-key sk-dashscopeYYYY]    # 可选，视觉模型用
```

脚本自动：装 git/docker/python3 → clone 源码到 `/opt/doskill` → 写 `.env` → 起 mysql 容器 → systemd 起 orchestrator + llm-proxy → 起 main demo → 打印 admin token + orchestrator URL。

末尾你会看到：

```
════════════════════════════════════════════════════════
  doskill 部署完成
  Orchestrator URL: http://<公网 IP>:9000
  Admin Token: <一行长字符串>
════════════════════════════════════════════════════════
```

把这两项填进扩展即可。详细的 ECS 准备清单 + 老式手动部署见 `deploy/README.md`。

### 加载 Chrome 扩展

```bash
cd extension && npm install && npm run build
```

`chrome://extensions/` → 开「开发者模式」→「加载已解压的扩展程序」→ 选 `extension/dist/`。

扩展图标右键 → 「打开侧边栏」。

---

## 使用流程 E2E

1. **打开 main demo** —— 浏览器到 `http://<部署地址>:5199/orders`（业务员的「样板间」）。
2. **输入需求** —— 侧边栏底部输入框打字，例：「未发货徽章改红色」。
3. **加附件 / 框选**（可选）—— 点 📍 框选改的区域，或粘贴图片，或附文件。最多 3 张。
4. **提交** —— 扩展把截图 + URL + 坐标 + 需求 POST 到 orchestrator。
5. **看 SSE 流式日志** —— 澄清问答 → 路由解析 → opencode 改代码（行级实时滚动）→ vite build → docker 起预览 → 预览就绪。
6. **看预览** —— 「↗ 新标签打开」检查效果。觉得没改对就直接说「字号再大一点」继续 refine。
7. **合并** —— 满意点「确认合并 →」→ rebase 回 main + 重建 main demo → `:5199` 自动刷出新 UI。

中间任何阶段卡住，看 SSE log 里的「⏳ ... 已 Xs」心跳判断是 LLM 慢 / runner 跑得久 / 真死了。

---

## 配置

### 部署级配置（`deploy/.env`，少改）

只 3 个字段必填，其它保持 `env.example` 默认：

```bash
LLM_API_KEY=sk-deepseek...    # DeepSeek API key（dev runner + 澄清模型都用这个走 LiteLLM）
ECS_HOST=<公网 IP 或 127.0.0.1>
PREVIEW_HOST=<同上，拼预览 URL 用>
```

⚠️ `.env` 注释**只能独占一行**，不能写在 `KEY=value` 同一行（详见 [TROUBLESHOOTING #1](docs/TROUBLESHOOTING.md#1-env-行内--注释被-systemd-当成-value-字面量)）。

### 运行时配置（扩展 Settings Panel，常改）

侧边栏右上角齿轮 → 4 个折叠分组：

| 分组 | 改什么 | 备注 |
|---|---|---|
| 服务器连接 | Orchestrator URL + admin token | 本地存 chrome.storage |
| AI 模型 | dev_runner / dev_model / vision_model | PUT `/admin/config` 写 DB |
| API key | DeepSeek + DashScope | 改了自动重启 LiteLLM |
| 项目路径 | demo_repo_path / preview_backend_url | DB 字段，无 ssh 即改即生效 |

每个字段右边 ❓ 是 HelpBubble，点开有详细说明 + 排障引导。日常运维不再 ssh ECS / 改 `.env` / 手动重启。

### 多项目切换

扩展头部下拉切项目，每个项目自带独立的 orchestrator 地址 / token / 模型配置。新项目走「Create Project」走 AI 助手部署流程。

---

## 项目结构

```
demo/             被改的目标产品（订单管理 mini app）
  backend/        FastAPI + SQLAlchemy + MySQL
  frontend/       React 19 + Vite 6 + react-router 7
  AGENTS.md       /init 自动生成的项目知识文档（给 LLM 用，业务员看不见）

orchestrator/     FastAPI 单体（386 项 pytest 通过）
  src/orchestrator/
    main.py                REST + SSE + lifespan
    admin.py               /admin/config CRUD + LiteLLM 自动重启
    auth.py                admin token 校验中间件
    pipeline.py            FSM 驱动器 + heartbeat + _spawn 后台 task
    git_manager.py         create_branch / merge_to_main（带 stash 兜底）
    repo_init.py           opencode 扫仓库写 AGENTS.md
    interaction_channel.py SSE 桥接 + question-resolved 广播
    repository.py          DB 层（SQLAlchemy）
    models.py              ORM models（change_request / conversation / system_config）
    events.py              EventBus（按 request_id 分发 + 重连回放）
    quota.py               槽位管理（QUOTA_SIZE=5）
    reaper.py              闲置 CR 回收（IDLE_TTL=1800s）
    compaction.py          对话动态压缩（>40k tokens 自动 summarize 老消息）
    intent_classifier.py   new_cr / refine_cr / chat_only LLM 路由
    chat_responder.py      chat_only 路径回复器（不进 pipeline）
    self_heal.py           失败后 LLM 决定 retry / revise / escalate（MAX=2）
    conversation.py        多轮对话持久化
    multi_repo.py          多项目支持
    system_config.py       运行时配置（dev_model / api_key / repo path 等）
    states.py              FSM 状态常量
    schemas.py             Pydantic（MAX_ATTACHMENTS_PER_MESSAGE=3）
    history_writer.py      spec/plan/result 沉淀到 .doskill/history
    config.py              Settings（preview_port_min=5100 / max=5199）
    adapters/
      interfaces.py        4 个 Protocol
      impl/
        brainstorming_skill.py   InteractionSkill（_HARD_ROUND_CAP=12，业务员按「✓ 够了」可提前停）
        react_vite_stack.py      StackAdapter（vite build）
        ui_label_extractor.py    实时 grep 前端源码拿真实 UI 标签喂澄清 prompt
        opencode_runner.py       DevRunner（默认）
        claude_code_runner.py    DevRunner（备选，跟 DeepSeek thinking mode 不兼容）
        docker_preview.py        PreviewAdapter（每 CR 一个容器，端口 5100-5199）
        _llm.py                  complete / complete_vision / complete_vision_stream
        _dev_runner_common.py    stream_subprocess + commit_all

extension/        Chrome MV3 扩展（292 项 vitest 通过 + 9 skipped）
  src/
    background/
      service-worker.ts   多 CR 镜像 + SSE 订阅 + chrome.alarms keepalive（30s 心跳防回收）
      request-store.ts    纯 reducer + chrome.storage 持久化
    content/
      capture-overlay.ts  框选 overlay + OffscreenCanvas JPEG@0.75 压缩
    ui/
      App.tsx             顶层 shell：根据 config / FSM 状态路由到具体面板
      panels/             高层面板（项目级 / 配置级）
        ChatPanel.tsx                主对话流（输入 + 多附件 + SSE 实时回灌）
        DeploymentAssistantPanel.tsx Plan 7 AI 助手引导部署（DeepSeek 对话 + ActionCard）
        CreateProjectPanel.tsx       新建项目 step 1 项目名 → step 2 跳 DeploymentAssistant
        ProjectSelectorPanel.tsx     首次安装选已有项目
        SettingsPanel.tsx            日常配置（4 折叠分组：服务器 / 模型 / API key / 路径）
        SetupWizardPanel.tsx         ⚠ 老 4 步向导，已不被 App 路由（被 DeploymentAssistant 取代）
      panels.tsx          按 CR FSM 状态切换的 body 面板
        ClarifyPanel / FormPanel / VariantsPanel / StatusPanel / FailedPanel
        CapturePanel / ReviewCapturePanel / PreviewPanel（Plan 10 后框选走 chip 路径，部分降级）
      components/         AgentTabBar / HistoryDropdown / AttachmentTray / PreviewDock / ChatInputBar / ChatStream ...
    ai/                   部署助手（Plan 7）
      DeepSeekClient.ts   ↔ DeepSeek REST + streaming
      DeploymentState.ts  对话 FSM（gathering_key → choosing_path → ... → done）
      actions.ts          ActionCard 类型（copy_command / capture_field / validate / transition）
      prompts/            path-local.md / path-ecs.md / examples-good.md / systemPrompt
    lib/
      orchestrator-client.ts  REST + SSE 客户端
      attachments.ts          MAX_ATTACHMENTS=3
      tabs.ts                 MAX_OPEN_TABS=8（LRU evict）
      config.ts               扩展 config schema（出厂 devModel=flash）

deploy/           ECS / 本地部署
  ecs-bootstrap.sh    🆕 业务员一键装（curl + sudo bash）
  local.sh            🆕 本地 docker 一键起
  deploy.sh           老式 rsync + ssh + systemd（开发者发新版本用）
  healthcheck.sh      11 项验证
  main-demo.sh        起 / 重建 main demo 容器
  rollback.sh         回滚到上一个版本
  systemd/            doskill-orchestrator + doskill-llm-proxy
  llm-proxy/          LiteLLM 路由配置

docs/
  superpowers/specs/  设计文档
  superpowers/plans/  5 个阶段的实现计划
  RUNBOOK.md          ECS 日常运维
  TROUBLESHOOTING.md  21 个踩过的坑（每条带根因 + 修法）
  mockups/            静态 HTML demo（无后端，看 UI 流）
```

---

## 开发

### 本地跑测试

```bash
# orchestrator
cd orchestrator
python3 -m venv venv && venv/bin/pip install -e ".[dev]"
venv/bin/pytest                          # 386 项

# extension
cd extension
npm install && npm test                  # 292 项 vitest（9 skipped）
npm run build                            # 产物在 dist/

# demo
cd demo/backend && python3 -m venv venv && venv/bin/pip install -e .
cd demo/frontend && npm install
```

### 不起 ECS 看 UI

```bash
open docs/mockups/doskill-extension-demo.html    # 静态 HTML 体验扩展交互流
```

### 设计原则

- **状态机驱动**：每条 CR 在 FSM 上跑一遍，状态写 DB + 广播 SSE，前端是 mirror reducer。
- **分支 + 容器即隔离单位**：每条 CR 占一个 `cr/<id>` 分支 + 一个独立预览容器。
- **Adapter 是唯一的「栈相关」代码**：换栈不动 Orchestrator 主体。
- **OUT of scope**（设计 §8）：多角色 / 多租户 / 多栈 / 鉴权权限 / 复杂 RBAC —— MVP 不做。

### 改 adapter

换 dev runner / 换栈 / 换预览方式只动 `orchestrator/src/orchestrator/adapters/impl/` 里对应文件。`interfaces.py` 是契约，Pipeline 主体 + 测试都不用改。

---

## 相关文档

- 📐 [设计文档](docs/superpowers/specs/2026-05-14-ai-native-low-code-design.md) —— 决策记录 + 完整架构推导
- 📋 [更新日志](CHANGELOG.md) —— 版本历史 + 每个 Plan 的变更
- 🚀 [部署手册](deploy/README.md) —— ECS 部署细节 + 回滚步骤
- 🛠️ [运维 RUNBOOK](docs/RUNBOOK.md) —— 日志 / 重启 / 改配置生效
- 🪤 [踩过的坑](docs/TROUBLESHOOTING.md) —— 21 个真实问题 + 根因 + 修法
- 🎬 [扩展静态 demo](docs/mockups/doskill-extension-demo.html) —— 不起后端看交互流

---

## License

私有，未授权。
