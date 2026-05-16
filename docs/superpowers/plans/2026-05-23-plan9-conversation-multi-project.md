# Plan 9 — 多项目 + 持久 conversation + 动态压缩 + 预览浮卡

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把扩展从「一次性窗口」改成「Cursor 式持续对话工作台」。三个绑在一起的能力：

1. **多项目** —— 扩展端按「项目」组织配置。每个项目独立绑一个 orchestrator + git 仓库 + API key（用户选择「一个 orchestrator 服一个 project」）。head 加项目切换 dropdown，新建项目走 Plan 7 部署助手。
2. **持久 conversation** —— Conversation = N 个 CR 的容器。orchestrator DB 加 `Conversation` 表 + `ChangeRequest.conversation_id` 外键。同一 conversation 里 chat 上下文连续保留，业务员说完「订单徽章改红」AI 做完后，可以继续说「字号再大一点」，AI 看到完整历史接着做。conversation 永不过期（除非手动删），3 天后回来还能切回继续聊。
3. **动态压缩 + 预览浮卡** —— 对话长了不暴力截断，server 在发给 dev runner 前用 LLM 把老消息压成摘要（参考 Claude Code），UI 完整显示原文。预览 + 合并/丢弃改成**底部 dock 浮卡** —— 业务员看预览的同时输入框可继续聊，触发新一轮 CR，浮卡 url 自动更新。合并按钮一直可用。

**Architecture:**
- **Project（extension-only）**：`chrome.storage.local` key `doskill_projects: Project[]` + `doskill_active_project_id: string`。Project = `{ id, name, config: Config, createdAt }`。Config 沿用 Plan 6 的字段（orchestratorUrl + adminToken + ... ）。一个 project 对应一台远端 orchestrator。
- **Conversation（orchestrator DB）**：`Conversation(id, title, created_at, updated_at, archived_at, messages JSON)`。`messages` 是 append-only 列表，结构见 §数据契约。`ChangeRequest` 加 `conversation_id` FK + index。
- **持续对话 chat 模型**：MainShell 改成 `ChatPanel` 主体 + 顶部 `StatusBanner`（当前活跃 CR 的 FSM phase）+ 底部 `PreviewDock`（最近完成的 CR 预览 + 操作按钮）。业务员在 ChatPanel 输入新需求 → 起新 CR 挂到当前 conversation → SSE 流式回 chat 流 → 预览就绪后 PreviewDock 自动更新。
- **动态压缩**：pipeline 调 dev runner 前估算 tokens；超过软阈值 40k → 拉「保留窗口」之外的消息走 DeepSeek v4-flash 压成摘要，存为一条 `summary` message；超过硬阈值 56k → 当场压不能跳过。保留：最近 6 轮 user-AI pair + 所有 user 消息原文 + 活跃 CR 的完整 chat。
- **PreviewDock 同时只显示一条 CR 的预览**：默认是 conversation 里**最新有 preview_url 的 CR**（不管 merged / preview-ready / discarded）。业务员点 sidebar 切到老 CR 时浮卡跟着切。
- **迁移**：现有未挂 conversation_id 的 ChangeRequest 自动 bucket 到一个 "Legacy" conversation（per orchestrator 一个），不影响 v0.2/v0.3 数据。

**Tech Stack:** 沿用现有 ——orchestrator FastAPI + SQLAlchemy 2.x + pydantic v2 + alembic（如已配置）；extension React + Vite + zod。新加：服务端 token 估算用 [`tiktoken`](https://pypi.org/project/tiktoken/)；动态压缩用 DeepSeek v4-flash via llm-proxy（无新依赖）。前端 fetch 改用 EventSource 复用现有 SSE 通道。

---

## 前置约定（每个任务都假定已满足）

- Plan 6/7/8 已合并 main，v0.4.0-alpha 已发。
- **一个 orchestrator = 一个 project**：orchestrator DB 不加 Project 表；多项目是 extension 侧概念，每个 project 是「客户端绑哪台 orchestrator」。
- conversation 默认软删（archived_at），3 天后由 reaper job 硬删。
- 业务员**只能在 owner 的浏览器内**看 conversation —— conversation 没跨设备同步设计（chrome.storage.local 本地）；server 侧 conversation 跟随调它的 admin token，admin token 一致即同一所有者。
- 压缩 prompt 写中文（业务员看得懂摘要），LLM 走 DeepSeek v4-flash（成本低、强够用）。
- 现有 ConversationList 组件（Plan 4 起的「CR 列表」）改名 ChangeRequestSidebar；新写 ConversationSidebar 替代之前的入口。
- 在新分支 `plan9-conversation-multi-project` 上做。
- **子代理 isolation 注意**：harness 在 worktree 清理时会丢未 commit 的 working tree changes（v0.4 期间踩过坑）。子代理工作完务必 `git add -A && git commit` 到自己 branch，不然结果会丢。

## File Structure

```
orchestrator/
  src/orchestrator/
    models.py                        # 加 Conversation 表 + ChangeRequest.conversation_id FK
    conversation.py                  # Conversation ORM + repo
    compaction.py                    # estimate_tokens + compact()
    pipeline.py                      # 改：每条 CR 必挂 conversation_id；dev_runner 收到压缩后 history
    history_writer.py                # 写 message 时同步 append 到 conversation.messages JSON
    main.py                          # /conversations CRUD + /admin/migrations/bucket-legacy
    schemas.py                       # ConversationOut, MessageOut, CreateConversationIn
  tests/
    test_conversation_model.py
    test_conversation_endpoint.py
    test_compaction.py
    test_pipeline_uses_compacted_history.py
    test_migration_bucket_legacy.py

extension/
  src/
    lib/
      projects.ts                    # Project model + chrome.storage CRUD + active切换
      conversations.ts               # client：拉 conversation 列表 / 拉 messages / 起 CR
      types.ts                       # 加 Project / Conversation / Message 类型
    ui/
      App.tsx                        # 改：未选项目 → ProjectSelectorPanel；选了 → MainShell
      panels/
        ProjectSelectorPanel.tsx     # 项目列表 + 新建项目入口
        CreateProjectPanel.tsx       # 包 DeploymentAssistantPanel，完成时落到 projects
        MainShell.tsx                # 主壳：ProjectSwitcher + StatusBanner + ChatPanel + PreviewDock
      components/
        ProjectSwitcher.tsx          # head dropdown
        ConversationSidebar.tsx      # 替代旧 ConversationList，按 conversation 分组
        StatusBanner.tsx             # 顶部 sticky FSM 状态
        PreviewDock.tsx              # 底部 sticky 预览 + 合并/丢弃
        CompactedRangeNotice.tsx     # chat 里折叠条「已折叠 47 条历史 ▾」
  tests/
    project-switcher.test.tsx
    conversation-sidebar.test.tsx
    preview-dock.test.tsx
    main-shell-integration.test.tsx
    projects-storage.test.ts
    conversations-client.test.ts
```

---

## 数据契约（核心）

### orchestrator DB

```python
# orchestrator/src/orchestrator/models.py（新增）

class Conversation(Base):
    __tablename__ = "conversation"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)     # ulid-ish
    title: Mapped[str] = mapped_column(String(200))                   # 业务员看的名字（自动从首句生成）
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # append-only JSON 数组；item 见 MessageDict
    messages: Mapped[dict] = mapped_column(JSON, default=list)

class ChangeRequest(Base):
    # 已有字段...
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("conversation.id"), nullable=True, index=True,
    )
```

### Message JSON shape

```typescript
// orchestrator → extension wire（snake_case）；extension storage camelCase 同结构
type Message =
  | { type: "user";    ts: string; content: string }
  | { type: "ai";      ts: string; content: string; cr_id?: string }
  | { type: "summary"; ts: string; content: string; replaces_count: number; replaces_token_estimate: number };
```

- `user` / `ai` 是原始消息；`summary` 是压缩后插入的一条，代表它之后被替代的 N 条
- 永远 append；压缩时**不删** —— `summary` 只是新 entry，DB 里老 entry 仍在（UI 上 default 折起）
- `replaces_count` / `replaces_token_estimate` 用来在 UI 上画「已折叠 N 条 (~Xk tokens)」折叠条

### extension storage

```typescript
// extension/src/lib/projects.ts
const ProjectSchema = z.object({
  id: z.string().min(1),                       // ulid-ish
  name: z.string().min(1).max(50),             // 业务员起的名字（"订单管理项目"）
  config: ConfigSchema,                        // 复用 Plan 6 ConfigSchema
  createdAt: z.number().int(),                 // epoch ms
});

// chrome.storage.local keys:
//   doskill_projects: Project[]
//   doskill_active_project_id: string | null
//   （旧 doskill_config_v2 自动迁移成单个 project，标 name="默认项目"）
```

---

## 任务清单

> 每任务一个 commit；用 `superpowers:subagent-driven-development` 起子代理时让子代理 TDD（先红→再绿→再 refactor）。

### Task 1 — orchestrator Conversation 模型 + repository

- [ ] **Step 1: TDD 红** — `tests/test_conversation_model.py`：
  - `test_create_conversation_with_empty_messages`
  - `test_append_message_persists_in_order`
  - `test_archive_sets_archived_at`
  - `test_change_request_conversation_id_fk`
- [ ] **Step 2: alembic migration** —
  - `0007_add_conversation_table.py`（Conversation 表 + ChangeRequest.conversation_id 列 + index）
- [ ] **Step 3: 实现** —
  - `models.py` 加 Conversation
  - `conversation.py`：`ConversationRepository.create / get / list_active / archive / append_message`
  - `ChangeRequest.conversation_id` 字段
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): Conversation 表 + CR 关联`

### Task 2 — orchestrator /conversations REST + CR 关联

- [ ] **Step 1: TDD 红** — `tests/test_conversation_endpoint.py`：
  - `POST /conversations` 创建 → 返回 id + 空 messages
  - `GET /conversations?archived=false` 列出未归档
  - `GET /conversations/{id}/messages?since_ts=...` 取增量消息
  - `POST /conversations/{id}/archive` 软删
  - `POST /change-requests` 接收 `conversation_id` → CR 关联
- [ ] **Step 2: 实现** —
  - `main.py` 加路由
  - `schemas.py` 加 `ConversationOut / MessageOut / CreateConversationIn`
  - 现有 `POST /change-requests` body 加可选 `conversation_id`（无则自动创建一个）
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(orchestrator): /conversations CRUD + CR 接入`

### Task 3 — orchestrator history_writer 同步 conversation.messages

- [ ] **Step 1: TDD 红** — `tests/test_history_writer.py` 加：
  - `test_user_message_appended_to_conversation`
  - `test_ai_message_includes_cr_id`
  - `test_concurrent_appends_serialized_via_row_lock`（用 `SELECT ... FOR UPDATE` 或 SQLAlchemy `with_for_update`）
- [ ] **Step 2: 实现** — history_writer 在写 events 表的同时 append 到 conversation.messages（事务里做）
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(orchestrator): history_writer 同步 conversation.messages`

### Task 4 — orchestrator compaction 核心

- [ ] **Step 1: TDD 红** — `tests/test_compaction.py`：
  - `test_estimate_tokens_close_to_tiktoken`（实际 ±10% 即可）
  - `test_compact_keeps_recent_6_user_ai_pairs`
  - `test_compact_keeps_all_user_messages_full`（即使老的）
  - `test_compact_summary_inserted_before_kept_window`
  - `test_compact_idempotent_when_below_threshold`
  - `test_compact_calls_llm_with_chinese_summary_prompt`
- [ ] **Step 2: 实现** `orchestrator/src/orchestrator/compaction.py` —
  - `estimate_tokens(messages: list[dict]) -> int`：用 tiktoken `cl100k_base`
  - `async def compact(conversation: Conversation, threshold_soft=40_000, threshold_hard=56_000) -> Conversation`
    - 算总 token；< soft → 返回原样
    - 切「保留窗口」：所有 `user` 消息 + 最近 6 个 user-AI pair + 活跃 CR 的所有消息（活跃 = 状态非终结）
    - 「可压」= 其他 ai 消息
    - 拿可压消息原文给 LLM（DeepSeek v4-flash via llm-proxy）：用 §压缩 prompt 模板
    - 把返回的摘要包成 `summary` message append；老 messages **不删**（UI 默认折叠）
    - 返回 compaction 后的 messages 视图（不写回 DB —— 调用方决定写不写）
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(orchestrator): compaction 估 token + 动态摘要`

### 压缩 prompt 模板（写在 `orchestrator/src/orchestrator/compaction.py` 内常量）

```python
COMPACTION_PROMPT = """\
你是 doskill 对话压缩器。下面是业务员和 AI 的多轮对话。

要求：把所有 AI 回复（除了被标记 [PRESERVE] 的）压成一段中文摘要，包含：
1) 业务员的核心意图演化（按时间顺序串出来）
2) 已完成的修改（每个一句，引用 cr_xxx）
3) 未决问题 / 业务员的偏好

约束：
- ≤ 800 字
- 不省决策（"业务员选了方向 A"），只省过程（"AI 正在改..."）
- 输出纯文本，没有 markdown 装饰

对话：
{messages}
"""
```

### Task 5 — orchestrator pipeline 集成 compaction

- [ ] **Step 1: TDD 红** — `tests/test_pipeline_uses_compacted_history.py`：
  - `test_dev_runner_receives_compacted_messages_when_over_threshold`
  - `test_dev_runner_receives_raw_when_under_threshold`
  - `test_summary_persisted_to_conversation`
- [ ] **Step 2: 实现** — pipeline 在 dev_runner.invoke 前调 `compact(conversation)`；若返回带新 summary 则先 history_writer.append；dev_runner 收到压缩后的 history JSON
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(orchestrator): pipeline 调用前自动压缩`

### Task 6 — orchestrator Legacy CR 迁移

- [ ] **Step 1: TDD 红** — `tests/test_migration_bucket_legacy.py`：
  - `test_existing_cr_without_conversation_get_bucketed_into_one_legacy`
  - `test_legacy_conversation_messages_reconstructed_from_events`
- [ ] **Step 2: 实现** —
  - alembic migration `0008_bucket_legacy_crs.py`：创建一个 `Legacy` conversation；所有 `conversation_id IS NULL` 的 CR 关联进去
  - `history_writer` 写一段「reconstruct from events」逻辑（best-effort，从 events 表把 user 消息 + ai 消息按 ts 重排）
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(orchestrator): 老 CR 自动 bucket 到 Legacy conversation`

### Task 7 — extension Project model + storage

- [ ] **Step 1: TDD 红** — `tests/projects-storage.test.ts`：
  - `loadProjects / saveProject / setActive / deleteProject` 各 1
  - 迁移：老 `doskill_config_v2` 单 config → 自动包成一个 project，name="默认项目"，active
  - 删除最后一个 project → active 设 null，触发回到 ProjectSelectorPanel
- [ ] **Step 2: 实现** `extension/src/lib/projects.ts`：
  - zod ProjectSchema
  - `loadProjects() / loadActiveProject() / saveProject(p) / setActiveProject(id) / deleteProject(id)`
  - 迁移逻辑放 `migrateLegacyConfig()`，App boot 时调一次
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(extension): Project model + storage + 迁移`

### Task 8 — extension ProjectSwitcher + ProjectSelectorPanel + CreateProjectPanel

- [ ] **Step 1: TDD 红** —
  - `tests/project-switcher.test.tsx`：dropdown 展开列出 N 个项目；点切换；trash 按钮带 confirm
  - `tests/project-selector-panel.test.tsx`：列项目 + 「+ 新建」按钮
- [ ] **Step 2: 实现** —
  - `ui/components/ProjectSwitcher.tsx`：head 下拉 + active 高亮 + trash
  - `ui/panels/ProjectSelectorPanel.tsx`：未选项目时展示
  - `ui/panels/CreateProjectPanel.tsx`：包 DeploymentAssistantPanel，`onComplete` 时调 `saveProject` + `setActiveProject`，再回退
- [ ] **Step 3: 集成 App.tsx 路由** —
  - 未选 project → ProjectSelectorPanel
  - 选了 project 但 config 不全 → CreateProjectPanel（直接进部署助手）
  - 选了 project 且 config 全 → MainShell
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): 多项目切换 + 新建项目入口`

### Task 9 — extension MainShell 改持续对话 + PreviewDock

- [ ] **Step 1: TDD 红** —
  - `tests/main-shell-integration.test.tsx`：
    - 渲染 ChatPanel + StatusBanner + PreviewDock 三块；resize 时 chat 列表保留 scroll
    - 业务员在 input 打字 → 调 client.startChangeRequest → SSE 流式回填 chat
    - PreviewDock 在 preview-ready 出现 + 链接 + 合并/丢弃
    - 业务员在 preview-ready 时**继续输入新需求** → 起新 CR（同 conversation_id）→ PreviewDock url 更新
  - `tests/preview-dock.test.tsx`：渲染 url / branch / merge / discard 按钮；切换 active CR 时刷新
- [ ] **Step 2: 实现** —
  - `ui/panels/MainShell.tsx`：3 块布局
  - `ui/components/StatusBanner.tsx`：顶部 sticky；显示当前活跃 CR 的 phase（沿用 ProgressTrail 风格但横排）
  - `ui/components/PreviewDock.tsx`：底部 sticky；显示 conversation 里最近有 preview_url 的 CR
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(extension): MainShell 持续对话 + PreviewDock`

### Task 10 — extension ConversationSidebar + 客户端 conversations client

- [ ] **Step 1: TDD 红** —
  - `tests/conversation-sidebar.test.tsx`：按 conversation 分组；每行展开看 CRs；新对话按钮
  - `tests/conversations-client.test.ts`：listConversations / getMessages / createConversation / archive 各 1
- [ ] **Step 2: 实现** —
  - `lib/conversations.ts`：fetch wrappers + zod 校验
  - `ui/components/ConversationSidebar.tsx`：替代旧 ConversationList（保留 ChangeRequestSidebar 作 expand 子项）
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(extension): ConversationSidebar + client`

### Task 11 — extension CompactedRangeNotice + 折叠条 UX

- [ ] **Step 1: TDD 红** — `tests/compacted-range-notice.test.tsx`：
  - 渲染「已折叠 47 条历史（~12k tokens）▾」
  - 点击 → 展开 drawer 看完整老消息
  - 关掉浏览器再开 → 折叠状态保留（drawer 默认折）
- [ ] **Step 2: 实现** `ui/components/CompactedRangeNotice.tsx` + 在 ChatPanel 渲染 messages 时插入到 summary message 位置
- [ ] **Step 3: GREEN**
- [ ] **Step 4: 提交** — `feat(extension): 压缩历史折叠条 UX`

### Task 12 — E2E + 真人试跑 + v0.5.0

- [ ] **Step 1: orchestrator + extension 端到端 happy path** —
  - 起本地 orchestrator（`bash deploy/local.sh`）
  - 开扩展，新建项目「test」走 Plan 7 助手跑通
  - 起 conversation 说「改订单徽章」→ 见 preview → 不合并直接说「字号大点」→ 浮卡 url 变 → 合并 → 接着说「再加 loading 态」→ 又一个 CR
  - 关浏览器；重开；切回 conversation → 完整 history 在 + 输入框可继续
- [ ] **Step 2: 真人 E2E** —
  - 找 ≥ 5 人，每人在自己机器上走 happy path 一遍（用 Path A 本地 docker）；目标成功率 ≥ 80%
  - 卡点回到 Plan 7 prompt 或 PreviewDock UX 调
- [ ] **Step 3: 文档** —
  - `README.md` 更新「多项目 + 持续对话」概念图
  - `docs/CONVERSATION-MODEL.md` 单独一篇解释 conversation / compaction / project 关系
- [ ] **Step 4: tag** — v0.5.0；CHANGELOG.md 记录
- [ ] **Step 5: 提交** — `test(extension+orchestrator): Plan 9 E2E + 文档 + v0.5.0`

---

## 验收标准（Plan 9 完成定义）

- [ ] 业务员可以在扩展里**添加 / 切换 / 删除项目**；每个项目独立绑 orchestrator + git + key
- [ ] 同一 project 里能起多个 conversation；每个 conversation = N 个 CR
- [ ] 在 preview-ready 状态下**继续打字** → 起新一轮 CR 挂同一 conversation；PreviewDock 自动跟着切到新 CR；老 CR 仍可手动切回看
- [ ] 关浏览器、3 天后再开 → 切回老 conversation → 完整 chat history + 老 CRs 都在；继续聊 AI 仍然有上下文
- [ ] 对话长到 40k tokens → server 自动压缩并 append `summary` message；UI 显示折叠条；展开看完整老消息
- [ ] 老用户（v0.4 已部署）升级到 v0.5 → 自动迁移：原 `doskill_config_v2` 包成「默认项目」；原 CR 自动 bucket 到「Legacy」conversation；不丢数据
- [ ] 不依赖跨设备同步（chrome.storage.local 本地）—— 换机器要重新填项目
- [ ] 测试：orchestrator ≥ 250 passed；extension ≥ 240 passed
- [ ] v0.5.0 tag + README 更新 + CHANGELOG

---

## 关键不做（明确不在本 plan 范围）

- **跨设备 conversation 同步**：留给将来用 orchestrator 自己当存储 + 加端到端加密
- **conversation 模板 / 工作流编排**：本 plan 只是「持续对话」；不做「保存对话作为模板复用」
- **多 user / 协作**：admin token 仍是单用户。多人协同留给后续
- **conversation 命名 AI 化**：title 自动取首句 user 消息前 50 字；不调 LLM 起名（省钱）
- **压缩历史的删除**：summary 不替换老消息，DB 永远保完整；不做「压缩后真删」（存储不是瓶颈）
- **PreviewDock 同时显示多个预览**：永远 1 个，业务员要看老的就点 sidebar 切

---

## 风险 + 缓解

- **conversation.messages JSON 写并发**：用 SQLAlchemy `with_for_update` 行锁 + 短事务。或者改用专表 `Message(conversation_id, ts, type, content)` 避免 JSON 写竞态（看 Task 1 实现 retro，必要时改 schema）。
- **压缩 LLM 调用慢**：DeepSeek v4-flash 平均 < 5s；遇到 > 30s 超时 → fallback「无压缩直接发」+ log warning（不阻塞业务员）。
- **老用户迁移失败**：迁移逻辑用 try/except 包；失败时退化成「新建一个空 Legacy conversation」不影响新流程。在 boot 时 toast 提示用户「老对话迁移失败，可联系作者捞回」。
- **PreviewDock 抢空间**：移动浏览器 / 窄侧栏（< 320px）时 dock 折成小按钮（floating action button），点开 modal 看预览。
- **压缩误删用户偏好**：保留 100% user 消息原文 + 「不省决策只省过程」prompt 约束。即使误压，user 原意图全在。

---

## 需要用户提供（运行 Plan 9 前的一次性清单）

1. **确认 conversation 命名规则**：默认「首句 user 消息前 50 字」可以吗？还是想让 AI 起 3-5 字短标题（贵 ¥0.001/对话）？
2. **确认压缩阈值**：默认 40k 软 / 56k 硬适合 DeepSeek v4-pro 64k。如果未来换 v4-flash 32k 上下文，要不要改成 20k / 28k？
3. **真人 E2E 找 ≥ 5 人**：4 个跑通才达标。失败率 > 20% 触发 PreviewDock UX 或 prompt 迭代再发。

---

## 与 v0.2-v0.4 的关系

```
[v0.2 Plan 6] 自助配置（单 project + 老 wizard）
       │
[v0.3 Plan 7] AI 部署助手（单 project + assistant 替代 wizard）
       │
[v0.4 Plan 8] 多仓原子合并（仍是单 project，但项目里可以多个 git 仓库）
       │
[v0.5 Plan 9] 多项目 + conversation + 压缩 + 浮卡 ← 本 plan
       │       │
       │       ├ 多项目：每个 project 独立 orchestrator+git+key
       │       ├ conversation：N CR 串成持续对话，跨 session 持久
       │       ├ 动态压缩：长对话不爆 token
       │       └ 预览浮卡：preview 不再是流程终点
       │
[v0.6+] 后续：跨设备同步 / 协作 / 模板…
```

Plan 9 是把扩展从「工具」升级到「工作台」的关键一步。完成后用户体验对标 Cursor 侧边栏。
