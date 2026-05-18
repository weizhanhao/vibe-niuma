# Plan 7 — AI 部署助手（扩展即入口，对话即引导）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「装好扩展 → 跑通第一条 CR」之间的所有空白由一个**对话式 AI 助手**填上。用户装好扩展、只填一个 DeepSeek API key，剩下「在哪买服务器、怎么 ssh、怎么把 vibe-niuma 部署到 ECS、怎么把自己的 git 仓库接进来」全部由助手用自然语言引导完成。助手用 DeepSeek 直连（不依赖 orchestrator，因为这时候 orchestrator 还没起），输出**带结构化 action 的对话**：每一条「拷这条命令到你电脑跑」「打开这个网页注册」「把刚才命令的输出贴回来」都是一个可点击/可粘贴的卡片，而不是埋在 markdown 里要用户自己挖。

**助手的退出条件（核心契约）：** 当 orchestrator URL + admin token + DeepSeek key + git 仓库路径 4 项配齐、`/admin/config` PUT 成功、healthcheck 11/11 通过、第一条 CR 在 demo 仓库跑通预览，助手 panel **自动隐藏**，扩展进入 Plan 6 已有的正常流程（sidebar 列 CR + 框选 + 自然语言下单）。用户的原话：*「等部署所有的都搞完后，这个部署助手也就可以结束流程了，后续就是现在的功能了」*。

**Architecture:**
- **扩展端单页应用，零 orchestrator 依赖期**：助手运行时 orchestrator 可能还不存在。所有对话流走 `chrome.runtime` ↔ `DeepSeekClient`（直连 `https://api.deepseek.com/v1/chat/completions`），SSE 流式。这是**唯一**一处扩展直连 LLM 的代码路径，部署完后就走 orchestrator。
- **对话状态机** `DeploymentState`：`gathering_deepseek_key → choosing_path → collecting_info → executing → verifying → done`。状态机不替代 LLM 的判断，只做**软约束**：当 `done` 时隐藏 panel；当 `executing` 时禁用「重启对话」防误操作。
- **Action 协议**：DeepSeek 通过 system prompt 被约束输出 **`<actions>...</actions>` JSON 块** 夹在自然语言之间。前端解析这个块，渲染成可交互卡片：`copy_command`（一键复制 + 提示用户回贴输出）、`open_url`（在新窗口打开外链）、`capture_field`（直接写 chrome.storage）、`request_output`（要求用户粘贴命令输出，把贴的内容回喂给下一轮 LLM）、`validate`（前端去打 `/healthz` / `/admin/config` 验证）、`transition`（状态机跳转）。
- **两条预置「部署路径」对话**：Path A *本地 Docker*（适合先在自己机器试一下）、Path B *阿里云 ECS*（生产推荐）。Path A/B 的核心区别全在 system prompt 里 —— 同一个 LLM 客户端 + 不同 prompt = 不同向导。
- **沉淀知识库**：`extension/src/ai/prompts/*.md` —— `vibe-niuma-handbook.md`（一篇约 800 字的「vibe-niuma 是什么、为什么、配齐什么算成功」）+ `action-protocol.md`（约束输出格式）+ `path-local.md` + `path-ecs.md` + `examples-good.md` + `examples-bad.md`（few-shot）。Prompt 全部 bundle 进 dist，运行时拼接，不在网络上传。
- **安全**：DeepSeek API key 存 `chrome.storage.local`（同 Plan 6）。SSH 私钥**只接受 paste**，存 `chrome.storage.session`（浏览器关掉就丢），永远不发到任何远端，纯展示用「请把这个私钥放到 ~/.ssh/，跑 chmod 600」。**助手永远不在浏览器里 `exec`**，只生成命令交给用户拷贝。
- **重入**：用户中途关掉 panel / 浏览器，`vibe_niuma_deployment_state` 已存 chrome.storage，下次开扩展自动续上，DeepSeek 上下文从 `history` 数组复原（截掉最早的，保留最近 16 轮）。
- **退场**：`isConfigured() && isDeploymentVerified()` → App.tsx 把助手 panel 从路由表里摘掉。chrome.storage 留一个 flag `deploymentAssistantCompletedAt: number`，方便后续 telemetry。

**Tech Stack:** 扩展端 React + Vite + zod + react-markdown（已在 Plan 6 引入）。新加：DeepSeek SDK 不引入，纯 fetch + SSE 解析（避免一个临时依赖永久占用 bundle）。Prompt 用 `?raw` import bundle 成字符串。测试用 vitest + msw（mock DeepSeek SSE 响应）。

---

## 前置约定（每个任务都假定已满足）

- Plan 6 已合并 main（v0.2.0 已发）；扩展能存配置、`/admin/config` 已经在 orchestrator 跑通。
- 助手**只解决从零开始的部署**。已经部署完成的用户重装扩展时，会被 `isConfigured()` 直接路由进 Plan 6 的 MainShell，不进助手。
- 助手**不**替代 Plan 6 的 SettingsPanel —— 助手最后一步「把所有配置 PUT 进 orchestrator」其实就是调 SettingsPanel 已经写好的 `AdminClient.putConfig()`。用户后续修改配置仍走 Plan 6 SettingsPanel。
- 用户至少有一台能跑 Docker 的机器（Mac/Linux 笔记本 OR 阿里云 ECS）。Windows 用户走 WSL 路径，引导文案在 `path-local.md` 里有一段提示。
- 用户拿得到 DeepSeek API key（去 platform.deepseek.com 充值至少 ¥10）。这一条是助手的**唯一**前提，不省。
- DeepSeek 输出 token 用量计费由用户买单。一个完整部署会话预估 ≤ 8k tokens（约 ¥0.02）。Prompt 控制在 4k 内、对话历史 cap 16 轮即可保住预算。
- 在新分支 `plan7-ai-deployment-assistant` 上做。

## File Structure

```
extension/
  src/
    ai/
      DeepSeekClient.ts             # SSE 直连 deepseek，泛型 chat(messages) -> AsyncIterable<string>
      actions.ts                    # AiAction 类型 + parseActionsFromAssistant(text)
      DeploymentState.ts            # 状态机定义 + transition 函数（纯函数）
      systemPrompt.ts               # 把 *.md 拼成最终 system prompt
      prompts/
        vibe-niuma-handbook.md         # 「vibe-niuma 是什么 + 验收标准」
        action-protocol.md          # action JSON schema 描述（给 LLM 看的）
        path-local.md               # 本地 Docker 向导脚本
        path-ecs.md                 # 阿里云 ECS 向导脚本
        examples-good.md            # 3-5 个正例对话片段（few-shot）
        examples-bad.md             # 2-3 个反例（LLM 输出过长、忘加 actions 等）
    ui/
      panels/
        DeploymentAssistantPanel.tsx  # 主面板（左：聊天，右：进度 + 配置预览）
        ChatPanel.tsx                 # 消息列表 + 输入框（流式渲染）
      components/
        ActionCard.tsx              # 渲染单个 action（copy / open_url / paste-back / validate）
        DeploymentProgress.tsx      # 顶部进度条（5 个状态点）
    lib/
      deployment-state-store.ts     # chrome.storage 包装：load/save/reset
      ai-config.ts                  # DeepSeek key 单独存 ai_deepseek_key（与 Plan 6 server.deepseekApiKey 解耦）
  tests/
    deepseek-client.test.ts         # SSE 解析、错误重试、AbortSignal
    actions-parser.test.ts          # 正常 JSON、嵌入文本、损坏 JSON 容错
    deployment-state.test.ts        # 5 个状态间的转换 + 不允许的转换
    deployment-assistant.test.tsx   # 完整路径：DeepSeek key → done
    chat-panel.test.tsx             # 流式渲染、abort、错误态
    action-card.test.tsx            # 复制按钮、外链、paste-back

orchestrator/                       # 本 plan 不改动
deploy/                             # 本 plan 不改动
```

## 状态机契约

```typescript
// extension/src/ai/DeploymentState.ts

type DeploymentState =
  | { phase: 'gathering_deepseek_key' }
  | { phase: 'choosing_path' }
  | { phase: 'collecting_info'; path: 'local' | 'ecs'; collected: Partial<CollectedInfo> }
  | { phase: 'executing'; path: 'local' | 'ecs'; collected: CollectedInfo; currentStep: string }
  | { phase: 'verifying'; path: 'local' | 'ecs'; orchestratorUrl: string; adminToken: string }
  | { phase: 'done'; completedAt: number };

type CollectedInfo = {
  // Path B (ECS) 才需要：
  ecsHost?: string;
  ecsUser?: string;
  sshPrivateKey?: string;       // chrome.storage.session，永不外传
  // Path A/B 共用：
  gitRepoUrl?: string | null;   // null = 用 demo 默认仓库
  dashscopeApiKey?: string;     // 可选
};

// 唯一允许的状态转换图：
// gathering_deepseek_key → choosing_path → collecting_info → executing → verifying → done
// 任何阶段 → gathering_deepseek_key（用户点「重新开始」，会清 chrome.storage 但保留 deepseekKey）
```

`transition(state, event)` 是**纯函数**，参考 Plan 2 的 FSM 测试套路（每个非法转换都有一个 negative test）。

## Action 协议契约（system prompt 的核心约束）

LLM 在每一轮回复结尾**必须**输出 `<actions>...</actions>` 块（即使是空 `<actions>[]</actions>`），前端正则提取后 JSON.parse。

```typescript
// extension/src/ai/actions.ts

type AiAction =
  | { type: 'copy_command'; label: string; command: string; expectsOutput: boolean }
  | { type: 'open_url'; label: string; url: string }
  | { type: 'capture_field'; field: keyof CollectedInfo; value: string }
  | { type: 'request_output'; placeholder: string }   // 要求用户粘贴上一条命令的输出
  | { type: 'validate'; kind: 'orchestrator_healthz' | 'admin_config'; url: string; token?: string }
  | { type: 'transition'; to: DeploymentState['phase'] };
```

`copy_command` 渲染成：「[一键复制] $ ssh root@... \"...\"」+ 「[完成 / 我跑出错了]」两个按钮。`expectsOutput: true` 时下一步必带 `request_output`（前端校验 LLM 没忘加）。

**反例校验**：解析器对损坏 JSON 容错：`<actions>...</actions>` 缺失 → 视为「等待用户输入」；JSON 无效 → 渲染原文 + 一行红字「AI 输出格式错误，请点重试」，不 crash。

---

## 任务清单

> 每个任务一个 commit；用 `superpowers:subagent-driven-development` 起子代理时让子代理写 TDD（先红→再绿→再 refactor）。

### Task 1 — DeepSeekClient（SSE 直连）

- [ ] **Step 1: TDD 红** — `tests/deepseek-client.test.ts`：
  - test 1: `chat([{role: 'user', content: 'hi'}])` 返回 AsyncIterable，依次 yield SSE chunk 的 `delta.content`，最后 finally 调 abort controller。
  - test 2: API 返回 401 → 抛 `DeepSeekAuthError`；429 → 自动指数退避重试 2 次；其他 4xx → 抛 `DeepSeekClientError`。
  - test 3: 用 msw mock SSE 响应，包含 `[DONE]` 终止符。
- [ ] **Step 2: 实现** `extension/src/ai/DeepSeekClient.ts`：
  - 构造接受 `{apiKey, model='deepseek-chat', baseUrl='https://api.deepseek.com/v1'}`
  - `chat(messages, {signal}): AsyncGenerator<string>` 用 fetch + ReadableStream + TextDecoder 解 SSE。
  - 不引入 deepseek SDK（避免锁定，纯 fetch 足够；DeepSeek 协议 OpenAI 兼容）。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 1 — DeepSeekClient SSE 直连`

### Task 2 — AI Action 解析器

- [ ] **Step 1: TDD 红** — `tests/actions-parser.test.ts`：
  - 正常 `<actions>[{type:'copy_command',...}]</actions>` → 返回数组。
  - 文本中混入 `<actions>` → 用正则只匹配最后一个。
  - JSON 损坏 → 返回 `{ok: false, raw: '...', error: '...'}`。
  - 空块 `<actions>[]</actions>` → 返回 `[]`。
- [ ] **Step 2: 实现** `extension/src/ai/actions.ts`：
  - zod schema 验证每个 action.type 的字段（discriminated union）。
  - `parseActionsFromAssistant(text): {prose: string, actions: AiAction[], parseError?: string}`，prose 是把 `<actions>` 块剥掉后的纯文本。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 2 — action 解析器 + zod schema`

### Task 3 — DeploymentState 机

- [ ] **Step 1: TDD 红** — `tests/deployment-state.test.ts`：
  - 每个合法转换 1 个 test（5 个）。
  - 每个非法转换 1 个 test（至少 5 个：`done → choosing_path`、`gathering_deepseek_key → executing` 等）。
  - reset → 回到 `gathering_deepseek_key` 但保留 deepseekKey 字段（在 caller 层）。
- [ ] **Step 2: 实现** `extension/src/ai/DeploymentState.ts`：
  - `transition(state, event): DeploymentState | { error: string }`，纯函数。
  - 不在这里读 chrome.storage（storage 由 `deployment-state-store.ts` 负责）。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 3 — DeploymentState 纯函数 FSM`

### Task 4 — Prompt 拼装 + handbook 撰写

- [ ] **Step 1: 写 6 篇 prompt markdown**（人类写作，不需要测试）：
  - `vibe-niuma-handbook.md`：800 字以内，告诉 LLM 「vibe-niuma 是给业务员用的低代码工具，部署完后用户在浏览器框选页面区域、说自然语言、AI 改代码、看预览、点合并。你的工作是把『装好扩展』到『跑通第一条 CR』之间的事情用对话形式完成」。明确**配齐什么算成功**（4 项 + healthcheck）。
  - `action-protocol.md`：把上面「Action 协议契约」用自然语言重述给 LLM 看，强调「每条回复必带 `<actions>`」「绝不在浏览器里 exec」「需要用户拷贝命令时用 `copy_command`」。
  - `path-local.md`：本地 Docker 流程脚本（git clone vibe-niuma → deploy/local.sh → 健康检查）。注意：本 plan **不**要求实现 local.sh，先让 LLM 引导用户跑现有的 deploy.sh（指向 localhost）。
  - `path-ecs.md`：阿里云 ECS 流程（买 ECS → 拿 IP/key → ssh → `bash <(curl -s ...)` bootstrap → 健康检查）。明确「SSH 私钥粘贴只用一次、永不发到任何地方」。
  - `examples-good.md`：3 段 few-shot 对话片段（用户「我刚装好扩展」→ 助手第 1 轮回复、用户回贴命令输出 → 助手第 2 轮回复 等）。
  - `examples-bad.md`：反例 —— 「不要这样：忘加 `<actions>`/把私钥写到 prompt 里/一次性甩 200 行命令让用户跑」。
- [ ] **Step 2: 实现** `extension/src/ai/systemPrompt.ts`：
  - `buildSystemPrompt(path: 'local' | 'ecs' | null): string`：handbook + action-protocol + 选中的 path-*.md + examples-good + examples-bad。
  - 用 `?raw` import bundle md 为字符串。
- [ ] **Step 3: 测试** — vitest 跑一个 `systemPrompt.test.ts` 校验拼装后 < 4000 tokens（用 `tiktoken`/`gpt-tokenizer` 估算；超了 fail）。
- [ ] **Step 4: 提交** — `docs(extension): Plan 7 Task 4 — handbook + action protocol + path prompts`

### Task 5 — ChatPanel（流式渲染）

- [ ] **Step 1: TDD 红** — `tests/chat-panel.test.tsx`：
  - 输入消息 → 立即追加 user message + 触发 deepseekClient.chat。
  - SSE 流逐 chunk 渲染（用 act + advanceTimers）。
  - abort：用户点「停止」→ chat AbortController 被 abort、UI 切回可输入态。
  - 错误态：DeepSeekAuthError → 显示「key 不对，去设置改」+ 一个跳转 SettingsPanel 的按钮。
- [ ] **Step 2: 实现** `extension/src/ui/panels/ChatPanel.tsx`：
  - 消息列表（user 蓝、assistant 灰、system tooltip 灰底深字）。
  - 流式：assistant 消息边追加边 markdown 渲染（react-markdown）；`<actions>` 块在渲染时跳过原文（解析后单独渲染卡片）。
  - 输入框：Enter 发送（Shift+Enter 换行），发送中禁用 + 显示「停止」。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 5 — ChatPanel 流式渲染`

### Task 6 — ActionCard 渲染器

- [ ] **Step 1: TDD 红** — `tests/action-card.test.tsx`：
  - copy_command：点「复制」调 `navigator.clipboard.writeText`、显示「已复制 ✓」3 秒。expectsOutput=true 时下方出现「粘贴输出」textarea。
  - open_url：点「打开」调 `window.open(url, '_blank', 'noopener')`。
  - capture_field：自动落 chrome.storage（不需要用户点）+ 在 UI 显示「已记录：ecsHost = ...」。
  - validate：点「验证」打 fetch、loading → 成功 / 失败 toast。
  - transition：自动触发状态机 transition（不渲染按钮，只显示「→ collecting_info」)。
- [ ] **Step 2: 实现** `extension/src/ui/components/ActionCard.tsx`：
  - discriminated union 渲染（switch on action.type）。
  - copy_command 用 navigator.clipboard.writeText，失败 fallback `document.execCommand`。
  - capture_field 写 chrome.storage 时先 zod 校验值；sshPrivateKey 单独写 `chrome.storage.session`。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 6 — ActionCard 6 种 action 渲染`

### Task 7 — DeploymentAssistantPanel 主面板 + 重入

- [ ] **Step 1: TDD 红** — `tests/deployment-assistant.test.tsx`：
  - 完整 happy path：填 DeepSeek key → 选 Path A → 跑命令 → paste-back → validate → done。每一步断言 chrome.storage 状态。
  - 重入：模拟「关掉 panel」→ 重新打开 → 状态机和消息历史复原。
  - reset：点「重新开始」→ 状态回到 gathering_deepseek_key、消息清空、deepseekKey 保留。
- [ ] **Step 2: 实现** `extension/src/ui/panels/DeploymentAssistantPanel.tsx`：
  - 顶部 DeploymentProgress（5 个点，当前 phase 高亮）。
  - 左 70% ChatPanel + 右 30% 收集进度卡（ecsHost / gitRepoUrl / ... 已收集字段勾选）。
  - 第一轮 system message 拼装：`buildSystemPrompt(state.phase === 'collecting_info' ? state.path : null)`。
  - 每条 assistant 消息渲染后：解析 actions → 对 `transition` 立即应用 → 对 `validate` 后置等待用户点。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 7 — DeploymentAssistantPanel + 重入`

### Task 8 — Path A：本地 Docker 路径联通

- [ ] **Step 1: 在 deploy/ 加一个 local.sh**（最小可跑）：
  - `deploy/local.sh`：把 deploy.sh 里 ECS-only 的部分（rsync、systemd）抽掉，留下「在本地起 mysql + llm-proxy + orchestrator」三个 docker run。生成 `.env.local` 时 `ECS_HOST=127.0.0.1`、`PREVIEW_HOST=localhost`。
  - 健康检查复用 `deploy/healthcheck.sh`（11 项已经在 Plan 6 通过）。
- [ ] **Step 2: 手动跑一次** —— 自己在 MacBook 上跑通 Path A 的完整 LLM 对话（不让别人测，自己当第一个用户），把卡住的地方回到 `path-local.md` 里补 example。
- [ ] **Step 3: 提交** — `feat(deploy): Plan 7 Task 8 — local.sh 本地 docker 部署路径`

### Task 9 — Path B：ECS 路径联通

- [ ] **Step 1: bootstrap 一键脚本** —— `deploy/ecs-bootstrap.sh`（**只能** ssh 进 ECS 后执行，不在 chrome 里跑）：
  - `curl -L https://raw.githubusercontent.com/<user>/vibe-niuma/main/deploy/ecs-bootstrap.sh | bash` （提示用户拷贝命令，不在浏览器里 exec）
  - 装 git/docker/python3 → git clone vibe-niuma → cp deploy/env.example deploy/.env → 引导用户编辑 → bash deploy.sh --full。
- [ ] **Step 2: 手动跑一次** —— 在一台**新开的** ECS（114.55.171.64 之外、最好是一台干净的 ESC 实例）上让助手把我引导一遍。卡 30 秒以上的地方都回到 `path-ecs.md` 改 prompt。
- [ ] **Step 3: 提交** — `feat(deploy): Plan 7 Task 9 — ECS bootstrap 一键脚本 + path-ecs prompt 调优`

### Task 10 — 退场逻辑 + App.tsx 路由

- [ ] **Step 1: TDD 红** — `tests/app-routing.test.tsx`：
  - `isConfigured() === false && !deploymentAssistantCompletedAt` → DeploymentAssistantPanel。
  - `isConfigured() === true` → MainShell（Plan 6 已有）。
  - 用户从助手完成「最后一步：PUT /admin/config」→ chrome.storage.onChanged 触发 → 自动切到 MainShell（不强制刷新页面）。
- [ ] **Step 2: 修改** `extension/src/ui/App.tsx`：
  - 已有的 loading / wizard / MainShell 三分支路由：把 wizard 分支替换成 DeploymentAssistantPanel。Plan 6 的 SetupWizardPanel **保留**但不再被路由命中 —— 留 1 个 release 周期作为 fallback（下个 plan 再删）。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 10 — App.tsx 路由切到部署助手 + 退场`

### Task 11 — 安全 + 速率限制 + 错误兜底

- [ ] **Step 1: 加固清单**：
  - DeepSeek key 不出现在任何 console.log / chrome.storage.local 的明文键名以外的地方。`grep deepseekKey` 全代码扫一遍，写 lint 规则禁止把它放 URL / 请求 body 外。
  - SSH 私钥：写 chrome.storage.session 时先校验「不是 .pem 文件名而是私钥内容」、长度 < 8 KB。展示时只显示前 5 + 后 5 字符（`abc...xyz`）+ 「点这里查看完整」（hover 1.5 秒才显示，防误截屏）。
  - DeepSeek 调用速率：1 用户 1 分钟最多 30 次 chat 调用，超出走 in-memory rate limiter（防 LLM 自己疯了循环调）。
  - 错误兜底：DeepSeek 网络挂了 → 显示「网络问题，30 秒后重试」+ 自动重试 1 次；持续失败 → 红 banner「先用 SettingsPanel 手填」+ 一键跳过助手。
- [ ] **Step 2: 测试** — `tests/security-hardening.test.ts`：私钥长度、key 不泄漏、rate limiter 各 1 个 test。
- [ ] **Step 3: 提交** — `feat(extension): Plan 7 Task 11 — 安全加固 + rate limit + 错误兜底`

### Task 12 — E2E + 真用户试跑

- [ ] **Step 1: 真人 E2E**（**不**用 Playwright，因为助手要用户拷命令 + 跑 docker，自动化反而失真）：
  - 找一个**完全没看过项目**的人（朋友、家人）按助手指引跑一遍 Path A。计时：从打开扩展到看到第一条 CR 预览 < 30 分钟。
  - 全程录屏 + 让用户口述每个卡点。卡点列表回到对应 prompt 修。
- [ ] **Step 2: 文档** — `README.md` 加一节「装好扩展 → 跑通第一条 CR」，3 行：装 → 助手会引导你 → 完事。把 Plan 6 README 里 ssh 命令删掉，因为助手会教。
- [ ] **Step 3: tag** — 部署成功率 ≥ 80%（5 个人跑、4 个跑通）→ tag v0.3.0。
- [ ] **Step 4: 提交** — `test(extension): Plan 7 Task 12 — 真人 E2E + README 收口 + v0.3.0`

---

## 验收标准（Plan 7 完成定义）

- [ ] 装好扩展、只填一个 DeepSeek key，助手能引导一个**完全不懂 docker 的人**在 30 分钟内跑通本地 Path A。
- [ ] 同样的助手能引导一个**有 ECS 但不熟运维的人**在 60 分钟内跑通 Path B。
- [ ] 助手生成的所有命令是「拷过去就能跑」的，**不**出现 `<your-server>` `<replace-me>` 占位符 —— 缺信息时必须用 `request_output` 问用户拿。
- [ ] 助手退场后再开扩展不会再弹出来（chrome.storage 有 `deploymentAssistantCompletedAt` 时间戳 → App.tsx 直接进 MainShell）。
- [ ] 用户中途关掉浏览器，下次打开能续上对话（chrome.storage 复原），且 SSH 私钥已经被清理（session storage 特性）。
- [ ] DeepSeek key 错 / 网络挂 / LLM 输出格式错全部有清晰的兜底文案 + 跳过助手按钮，绝不卡死。
- [ ] 助手**永远**不在浏览器里执行 shell 命令 —— 全部由用户拷贝执行（grep 代码：无 `eval` / `Function(` / `<script>`）。
- [ ] 一次完整部署对话的 DeepSeek token 用量 ≤ 8k（监控埋点）。
- [ ] v0.3.0 tag 创建，README 更新，CHANGELOG.md 记录。

---

## 关键不做（明确不在本 plan 范围）

- **多仓** —— 已挪到 Plan 8（`2026-05-22-plan8-multi-repo-atomic-merge.md`）。本 plan 仍单仓 demo 起步。
- **不替换 SettingsPanel** —— Plan 6 的 SettingsPanel 仍是日常配置入口；助手只解决从零到第一次跑通。
- **不做 voice / multimodal** —— 纯文本对话。截图、语音都留给后续 plan。
- **不引入 langchain / agno / ag2 等 agent 框架** —— 本助手就是一个「LLM + 状态机 + action 协议」的小有限状态机，引入框架反而拖累 bundle。
- **不做 telemetry 自动上报** —— 完成时间戳本地存 chrome.storage，不发到任何外部地址（隐私）。
- **不做付费计费** —— DeepSeek 用量由用户自己买单，扩展只展示「本次会话约花了 ¥0.0X」。
- **不做并发对话** —— 单 session、单状态机。用户关掉重开会续上同一会话，不开新会话。

---

## 风险 + 缓解

- **DeepSeek API 不稳定 / 限流** —— 已有重试 + 兜底跳过助手按钮。最差用户走 Plan 6 SettingsPanel 手填，损失体验但不阻塞。
- **LLM 幻觉「假命令」** —— Path A/B prompt 里精确给出真命令模板（不让 LLM 自由发挥）。few-shot 的 examples-good 里就有完整命令片段，LLM 大概率复用。E2E 时人工 catch。
- **bundle 体积膨胀** —— 6 个 .md 加起来预估 < 20 KB，gzip 后 < 6 KB；DeepSeek 客户端纯 fetch < 2 KB。整体 bundle 增量 < 10 KB（gzip），可接受。
- **SSH 私钥泄漏** —— chrome.storage.session + 屏幕显示掩码 + 永不出 fetch。代码 grep 测试守门。
- **用户中途换 path（Path A 跑一半切 Path B）** —— 状态机重置：清 collected 但保 deepseekKey；UI 给一个明确警告「重新开始会丢失当前进度」。

---

## 需要用户提供（运行 Plan 7 前的一次性清单）

1. **确认部署助手的 LLM 选型**：DeepSeek 默认（v4-flash 还是 v4-pro？v4-flash 便宜 5 倍但偶尔少加 actions；v4-pro 稳但慢）。**默认 v4-flash**，路径执行类对话不要求强推理。
2. **确认 Path A 的 docker 装机要求文案** —— 写 Mac / Linux / WSL 三栏指引（用户自己跑 docker 这事我们没法替他装）。
3. **真人 E2E 找 ≥ 5 个朋友试**：4 个跑通才算达标。失败率 > 20% 触发 prompt 迭代再发。

---

## 与 Plan 6 / Plan 8 的关系

```
[用户装扩展]
   ↓
[Plan 7 部署助手] ← 本 plan
   ↓ 一次性引导 → 写满 4 项配置 + healthcheck 11/11
   ↓
[Plan 6 主流程] ← 已有
   ↓ 框选 + CR + 预览 + 合并
   ↓
[Plan 8 多仓] ← 下一个
   ↓ 项目 = N 个 git 仓库 + compose 预览
```

Plan 7 是**一次性入门通道**：完成后自动隐藏、永不再弹（除非用户手动 reset）。Plan 8 是日常生产力升级，与 Plan 7 没有依赖。
