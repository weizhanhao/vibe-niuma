# Plan 10 — Cursor 式 Agent Tab + 连续对话 + 多附件 + 真多轮澄清

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把扩展从「每条输入 = 新 CR + 必带截图」升级成 Cursor 工作台体感 —— 顶部多 Agent Tab、conversation 内消息流连续累积上下文、附件可选可多个、AI 自适应多轮澄清问到位再写代码。

**为什么这么做（用户反馈直引）：**

> 「每次只要一输入就是一个新的对话，这不是我想要的。输入并不是一个新的需求 —— 一次输入可能是对你上次修改的评价，其实还在一个上下文。」
>
> 「也不是每次输入都要截图。一次输入只是想改一个页面，用户可以多输入几张图。」
>
> 「不要固定问最多三个问题，要到 AI 完全理解；最好让用户去选择，AI 根据代码和页面的理解让用户选，如果用户心目中没有答案也可以自定义。」
>
> 「Cursor 上面每个 tab 是一个会话，+ 号加新会话，时钟图标选择历史会话。」

**Architecture**：

- **Conversation = Cursor Tab**：顶部 tab bar 显示当前打开的 conversations，每个 tab 独立维护 message 流。`+` 新建，`×` 关闭（移出 tab 视图但 conversation 不归档），`🕐` 历史 dropdown。后端复用 Plan 9 的 `Conversation` 表 + `messages` JSON，前端 chrome.storage 持久 `openTabIds[] + activeTabId`。
- **Message ≠ CR**：一条 user message 在 server 端落 `conversation.messages` 作为消息记录；**视消息 intent 决定是否触发 CR**：
  - 「新需求」→ 起新 CR 走完整 pipeline（clarify → locate → code → build → preview）
  - 「调整 / refine」→ 不起新 CR，复用上一 CR 的 branch + entry_files + 累积 history，重跑 code → build → preview 覆盖同一 preview 容器
  - 「评价 / 讨论 / 问问题」→ 不进 pipeline，纯 LLM 回复 append 到 messages，无副作用
- **附件可选可多个**：`message.attachments[]`，每个 attachment 是 `{kind, screenshot_b64, mime, box?, viewport?, url?}`，`kind ∈ {screenshot_active_tab, framed_region, pasted_image, attached_file}`。打字直接发 = 0 附件 = follow-up；点「📎 框选当前页」加 framed_region；拖拽多张图加 pasted_image[]。**默认行为：不附 = 续上次的 context；附 = 这条 message 的新视觉锚点**。
- **真多轮澄清**：去掉 `max_questions=3` 硬上限，brainstorming_skill 改 `while not done` loop，每轮 LLM 拿全部已答 + 截图 + repo doc 重新评估 (a) 还有歧义吗 (b) 下一题问什么 (c) 给业务员哪 2-4 个 options。**LLM 强制返 options**，UI 选项按钮 + 自定义输入框并存。软上限 `MAX_SOFT_ROUNDS=8` 防贪心 + 业务员主动「✓ 够了直接干」按钮可终止 loop 进 located。
- **chat 流主体**：MainShell body 从「单 panel 状态机」改成「user/ai message 气泡流」（类 Cursor 侧边栏）。每条 message 显示作者 + 时间 + 内容 + 关联 CR 状态徽章 + 附件缩略图。FailedPanel/ClarifyPanel/VariantsPanel 等仍作为「内嵌交互卡片」插入 message 流相应位置（不是占满 body）。底部 ChatInputBar sticky（Plan 9 已实现）+ 附件区。
- **Pipeline 三路径**：`pipeline.run(request_id, mode)` 加 mode 入参 ∈ `{new_cr, refine_cr, chat_only}`。`refine_cr` 跳过 clarify/locate，把上 CR 的 branch + entry_file_contents 作为基础 ctx，dev_runner 收到「continue on branch」语义；`chat_only` 直接调 LLM 出回复 append messages 不进 quota / 不切 branch / 不起 docker。
- **意图分类用 LLM**（不是结构化规则）—— 业务员心智是「AI 已经了解我的项目，截图只是补充」，所以 **附件存在不强决定 mode**。intent_classifier 给 LLM 喂：(a) 最近 6 条 conversation 历史 (b) repo doc / AGENTS.md (c) 新消息文本 (d) 上一 CR 状态（preview-ready/failed/none），LLM 输出 `{mode: 'new_cr'|'refine_cr'|'chat_only', confidence: 0.0-1.0, reason: str}`。
  - `confidence ≥ 0.6`：直接走选定 mode，业务员无感
  - `confidence < 0.6`：UI 在输入框下方显微小提示「⨠ 我准备 refine 上一版（理由：你说"字号大一点"像是续改）」+「不对，起新一轮」按钮
  - 业务员 ⇧⌘↵ = 强制 `new_cr` 覆盖（最后手段）
  - **LLM 是兜底**：完全不需要业务员理解三种 mode；他只管说话，系统替他判
- **Tab + 历史 dropdown**：顶部 tab bar `[Tab 1 ×][Tab 2 ×][...][+][🕐][⚙]`。点 🕐 dropdown 列所有 active conversations + Archived 折叠组 + 顶部 Search 框。tab 上限 8（再开就 LRU evict 最老未活跃 tab）。
- **多 attachment 后端契约**：`POST /change-requests` 的 `screenshot_b64 + box_coords + viewport` 三字段 → `attachments: Attachment[]`；DB `change_requests.screenshot_b64` 字段保留兼容，新写入只取第一个 attachment；多附件全文存到 `change_requests.attachments JSON` 新列。**附件上限 3 张**（vision API token 安全区，超出 UI 拒绝并提示）。

- **失败自愈（Cursor 没有但业务员要）**：CR 跑挂时 pipeline **不直接告业务员**，先走 `self_heal` 路径：
  - LLM 看 `fail_log + 项目知识 + chat history + 失败 phase` → 判 (a) 是临时错（端口冲突 / docker race / 网络抖动）就 retry，(b) 是 prompt 不够清楚就改 prompt 再 retry，(c) 是环境问题（缺密钥 / 缺工具）超 AI 能力 → 立刻 escalate
  - 最多自愈 2 次（防死循环）；2 次仍败 → chat 流 inline 一个 FailCard：「我试了 X、Y 都没成功，需要你协助：建议 1) ... 2) ...」+「重试 / 我做了 X 你再试」两按钮
  - 业务员补充信息后下一条 message 走 `new_cr` 重启，他的协助内容塞进 prompt 上下文

**Tech Stack:** 沿用 —— orchestrator FastAPI + SQLAlchemy 2.x + pydantic v2；extension React + Vite + zod。无新依赖（多图 base64 + 流式 SSE + LLM Vision 都已就绪）。

---

## 前置约定（每个任务都假定已满足）

- Plan 9 已合 main，v0.5.0 已发并部署 ECS 跑通（commit `da4754c`）。
- ECS schema migration 已在 lifespan `_ensure_schema_migrations` 自适应处理，新加列继续走同一机制。
- 维持「一个 orchestrator = 一个 project」语义；多 tab 仅在前端切 conversation，不切 orchestrator。
- `dev_runner` adapter（opencode / claude-code）在 v0.5 已支持 `chat_history` 注入；refine 路径复用同字段 + branch 复用即可，不必每个 runner 重写。
- 多图给视觉模型一次性发：qwen-vl-plus 支持多图输入（按数组传），DeepSeek Vision 同支持。
- archived conversation 由 reaper 3 天后硬删（Plan 9 已实现）；tab close 只是 detach view，不归档。
- 回滚 ready：`deploy/rollback.sh` 已在 main，本 plan 加列继续 backward-compat（v0.5 不读新列即 safe）。

## File Structure

```
orchestrator/
  src/orchestrator/
    main.py                                  # 改：_ensure_schema_migrations 加 message 路径相关列；mode 入参透传
    models.py                                # 改：ChangeRequest.attachments JSON 列；mode 列；refine_of FK
    schemas.py                               # 改：MessageOut + Attachment schema + CreateMessage / CreateChangeRequest 支持 attachments
    conversation.py                          # 改：append_user_message + classify_intent helper
    pipeline.py                              # 改：Pipeline.run(request_id, mode); _run_new_cr / _run_refine_cr / _run_chat_only 三分支
    intent_classifier.py                     # 新：classify_message_intent(message, conversation) → 'new_cr' | 'refine_cr' | 'chat_only'
    chat_responder.py                        # 新：chat_only 路径走的 LLM 回复器（同 BrainstormingSkill 共用 LLM client）
    adapters/
      types.py                               # 改：DevContext 加 mode + base_branch + base_commit；Attachment dataclass
      interfaces.py                          # 改：DevRunner.run(repo, branch, ctx) 文档化 mode 语义
      impl/
        brainstorming_skill.py               # 改：clarify() while loop + 真多轮 + 强制 options + 主动跳出
        opencode_runner.py                   # 改：refine 模式拼 prompt 时带「续改 branch + 之前 diff + chat history」
        claude_code_runner.py                # 改：同上
        react_vite_stack.py                  # 改：build 时按 mode 决定是 fresh 还是 incremental
        docker_preview.py                    # 改：refine 时覆盖同 preview_handle，不起新容器（端口复用）
  tests/
    test_intent_classifier.py                # 新：6 条规则全覆盖（new_cr / refine_cr / chat_only / 强制 override）
    test_chat_responder.py                   # 新：纯 LLM 回复 + append messages + 不进 quota
    test_pipeline_modes.py                   # 新：mode=refine 复用 branch；mode=chat_only 不进 docker；mode=new_cr 保持现状
    test_multiturn_clarify.py                # 新：真多轮（done=false N 次 + done=true 收尾）+ 软上限 + 主动跳出 + options 透传
    test_attachments.py                      # 新：多 attachment 持久化 + dev_runner ctx 拿到所有
    test_schema_migration_plan10.py          # 新：lifespan 加 attachments / mode / refine_of 列幂等
extension/
  src/lib/
    types.ts                                 # 改：Message + Attachment + IntentMode types；ConversationTab type
    conversations.ts                         # 改：appendUserMessage / classifyIntent (client mirror) / listMessages
    tabs.ts                                  # 新：openTabIds + activeTabId 持久化 + LRU evict + tab CRUD
    attachments.ts                           # 新：framed_region / pasted_image / attached_file 收集器 + 数组管理
  src/background/
    service-worker.ts                        # 大改：SUBMIT 路径 → classifyIntent → 决定 new_cr/refine/chat_only；多 attachment 支持；tab mirrors
    orchestrator-client.ts                   # 改：createChangeRequest 接受 attachments 数组 + mode；新加 createMessage（chat_only 路径）
  src/ui/
    App.tsx                                  # 改：MainShell 改 Tab + chat 流；ChatInputBar 加附件区 + mode 切换
    components/
      ChatStream.tsx                         # 新：message 气泡流（user/ai/summary + 内嵌 ClarifyCard / VariantCard / PreviewCard / FailCard）
      AgentTabBar.tsx                        # 新：顶部 tab bar [Tab 1 ×][...][+][🕐][⚙]
      HistoryDropdown.tsx                    # 新：🕐 点开列所有 + Search + Archived 折叠
      AttachmentTray.tsx                     # 新：ChatInputBar 上方附件预览（缩略图 + × 删）
      ChatInputBar.tsx                       # 改：加附件区 + mode 切换开关 + 「+ 框选」/「📎 贴图」工具栏
      ChatBubble.tsx                         # 新：单条 message 渲染（含 attachment 缩略图 + 关联 CR 状态徽章）
      InlineCards/
        InlineClarifyCard.tsx                # 新：ClarifyPanel 改造成内嵌气泡（同时支持真多轮历史展示）
        InlineVariantCard.tsx                # 新：VariantsPanel 内嵌版
        InlinePreviewCard.tsx                # 新：preview-ready 时插入气泡（合并/丢弃 inline 按钮）
        InlineFailCard.tsx                   # 新：failed 时插入气泡（fail_phase + 重试按钮）
  tests/
    tabs-storage.test.ts                     # 新：tabs CRUD + LRU evict + active 切换
    attachments.test.ts                      # 新：framed_region / pasted_image / 多图组合
    intent-classifier-client.test.ts         # 新：client 端镜像 classifier 与 server 一致
    chat-stream.test.tsx                     # 新：渲染 message 流 + inline cards + 附件缩略图
    agent-tab-bar.test.tsx                   # 新：tab CRUD + history dropdown + 搜索
    chat-input-bar-attachments.test.tsx      # 新：附件 tray + mode 切换 + ⇧⌘↵ 强制新 CR
docs/
  CONVERSATION-MODEL.md                      # 新：解释 conversation / message / CR / tab 四个概念的关系（业务员视角图）
  PLAN10-INTENT-CLASSIFIER.md                # 新：意图分类规则表 + decision tree + override 矩阵
```

---

## 数据契约（核心）

### orchestrator DB

```
conversation (Plan 9 已有，不动)
  id VARCHAR(32) PK
  title VARCHAR(200)
  messages JSON
  created_at / updated_at / archived_at

change_requests (Plan 10 加列)
  ... 现有字段
  attachments JSON NULL              -- Plan 10：Attachment[] 数组（多图）
  mode VARCHAR(16) NULL              -- 'new_cr' | 'refine_cr'（chat_only 不产生 CR）
  refine_of VARCHAR(32) NULL FK→change_requests.id   -- refine 路径关联的上一 CR

-- conversation.messages JSON shape 扩展（向后兼容）
message {
  id: string                                 -- 短 ulid；服务端生成
  ts: ISO8601 string
  type: 'user' | 'ai' | 'summary' | 'system'
  content: string                            -- 文本
  attachments?: Attachment[]                 -- type=user 时可能有；ai/summary 不带
  cr_id?: string                             -- type=user 触发 CR 时关联；chat_only 没有
  cr_mode?: 'new_cr' | 'refine_cr'           -- 同上
  replaces_count?: number                    -- summary 用（Plan 9 已有）
  replaces_token_estimate?: number           -- 同上
  meta?: dict                                -- 自由字段（client 测试用）
}

Attachment {
  kind: 'framed_region' | 'screenshot_active_tab' | 'pasted_image' | 'attached_file'
  mime: string                               -- image/png | image/jpeg | image/webp | application/pdf
  b64: string                                -- base64 编码内容（PDF 也可以）
  url?: string                               -- 截图时业务员所在页 URL（locate 用）
  box?: { x, y, width, height }              -- 仅 framed_region 用
  viewport?: { width, height }               -- framed_region / screenshot_active_tab 用
  name?: string                              -- 上传文件原名
}
```

### extension chrome.storage.local

```
doskill_tabs_v1: {
  openTabIds: string[]                       -- conversation id 数组，顺序 = tab 显示顺序
  activeTabId: string | null
}                                            -- 上限 8 个；超出 LRU evict 最老未活跃

doskill_conversation_state_v1: Record<conversationId, {
  inputDraft: string                         -- 输入框未发送的草稿
  attachmentDrafts: Attachment[]             -- 未发送的附件
  scrollPosition: number                     -- chat 流滚动位置
}>
```

---

## 任务清单

> 严格 TDD：每个 task 先 RED 测试，再实现到 GREEN，再 commit。子 agent 跑务必 commit 进 worktree 才退（前面 Plan 7/8 踩过空 worktree 工作丢失的坑）。

### Task 1 — orchestrator schema 扩展 + lifespan idempotent migration

- [ ] **Step 1 RED**：`tests/test_schema_migration_plan10.py`
  - `test_ensure_attachments_column_added`
  - `test_ensure_mode_column_added`
  - `test_ensure_refine_of_column_added_with_fk`
  - `test_ensure_self_heal_attempts_column_added`
  - `test_migration_idempotent_second_run_noop`
- [ ] **Step 2 实现**：
  - `models.py`：`ChangeRequest` 加 `attachments: Mapped[list | None]`、`mode: Mapped[str | None]`、`refine_of: Mapped[str | None]` + FK to `change_requests.id`、`self_heal_attempts: Mapped[int]` (default 0)
  - `main.py`：`_ensure_schema_migrations` 列表加上面 4 列（沿用 v0.5 的 information_schema 检查逻辑）
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 1 — schema 扩展 attachments/mode/refine_of`

### Task 2 — orchestrator Attachment + Message Pydantic schemas

- [ ] **Step 1 RED**：`tests/test_attachments.py::test_attachment_schema_validates`
  - PNG b64 + box → ok
  - 缺 mime / kind 不在白名单 → ValidationError
  - PDF + kind=attached_file → ok（不要求 box）
- [ ] **Step 2 实现**：
  - `schemas.py`：`Attachment` pydantic model + `MessageOut` + 扩展 `CreateChangeRequestIn.attachments: list[Attachment] | None`（兼容老的 screenshot_b64 单字段：兜底转一个 attachment）
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 2 — Attachment + Message pydantic`

### Task 3 — orchestrator intent_classifier.py（LLM-based）

- [ ] **Step 1 RED**：`tests/test_intent_classifier.py`
  - `test_classify_uses_llm_with_conversation_history_in_prompt`
  - `test_classify_returns_mode_confidence_reason`
  - `test_classify_routes_continuation_phrase_to_refine`（mock LLM 返 refine + confidence 0.9）
  - `test_classify_routes_question_phrase_to_chat_only`
  - `test_classify_routes_new_intent_to_new_cr_regardless_of_attachments`
  - `test_classify_low_confidence_below_threshold_returns_unsure_flag`
  - `test_classify_explicit_override_force_new_cr_wins_over_llm`
  - `test_classify_uses_repo_doc_in_prompt`
  - `test_classify_falls_back_to_new_cr_on_llm_error`
- [ ] **Step 2 实现**：
  - `intent_classifier.py`：`async def classify(...) -> IntentDecision` (`{mode, confidence, reason}`)
  - LLM 走 `_llm.LLMClient.complete()`（轻量 text-only，不要 vision）；prompt 含：最近 6 条 chat history + repo doc 摘要 + 新消息 + 上一 CR 状态
  - LLM 返 JSON：`{"mode": "new_cr|refine_cr|chat_only", "confidence": 0.0-1.0, "reason": "..."}`
  - 解析失败 / LLM 错 → 兜底 `new_cr` + confidence=0.5
  - **附件不参与决策**（业务员明示：截图是补充，AI 应靠项目上下文判）
  - override 强制 mode 直接返
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 3 — LLM-based intent_classifier`

### Task 4 — orchestrator chat_responder.py（chat_only 路径）

- [ ] **Step 1 RED**：`tests/test_chat_responder.py`
  - `test_respond_appends_ai_message_to_conversation`
  - `test_respond_does_not_consume_quota`
  - `test_respond_does_not_create_change_request`
  - `test_respond_uses_compacted_chat_history`（接 Plan 9 compaction）
  - `test_respond_includes_repo_doc_in_prompt`
- [ ] **Step 2 实现**：
  - `chat_responder.py`：`ChatResponder` 类，`respond(conversation_id, user_message) → str`
  - prompt：「你是 doskill 助手。业务员在跟你聊 web 改造。回答以下消息，不要写代码、不要承诺改东西，只回答业务问题或建议」
  - 调 `_llm.LLMClient.complete()`（不带视觉，纯文本）
  - 写一条 `type=ai` message 到 `conversation.messages`
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 4 — chat_responder（chat_only 路径）`

### Task 5 — orchestrator pipeline.run(mode) 三分支

- [ ] **Step 1 RED**：`tests/test_pipeline_modes.py`
  - `test_mode_new_cr_runs_full_pipeline`（clarify → locate → code → build → preview，跟现状一致）
  - `test_mode_refine_cr_skips_clarify_and_locate`
  - `test_mode_refine_cr_reuses_base_branch`
  - `test_mode_refine_cr_reuses_base_preview_handle`
  - `test_mode_chat_only_does_not_create_cr`
  - `test_mode_chat_only_appends_ai_response_to_conversation`
- [ ] **Step 2 实现**：
  - `pipeline.py`：`Pipeline.run(request_id, *, mode: IntentMode = 'new_cr')`
  - 拆三个内部方法：`_run_new_cr` / `_run_refine_cr` / `_run_chat_only`
  - `_run_refine_cr`：
    - 从 `refine_of` 拿到上 CR 的 branch / locate_result / entry_file_contents
    - 直接进 coding phase；DevContext.mode='refine'；DevContext.base_branch=上 CR 的 branch；ctx.chat_history 含本次 user message
    - dev_runner.run 在 base_branch 上续 commit（git checkout 已存在 branch）
    - preview_adapter.serve 时传 base_handle 让 docker 复用同 container
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 5 — pipeline 三 mode 分支`

### Task 5b — orchestrator self_heal 失败自愈

- [ ] **Step 1 RED**：`tests/test_self_heal.py`
  - `test_self_heal_retries_temporary_error_max_2_times`
  - `test_self_heal_classifies_docker_port_conflict_as_retryable`
  - `test_self_heal_classifies_missing_api_key_as_escalate`
  - `test_self_heal_attempt_count_persists_to_cr`
  - `test_self_heal_failure_after_max_retries_appends_help_message_to_conversation`
  - `test_self_heal_help_message_includes_what_was_tried_and_what_user_should_do`
- [ ] **Step 2 实现**：
  - `self_heal.py`：`SelfHealClassifier`，给 fail_log + project context，LLM 返 `{action: 'retry'|'retry_with_revised_prompt'|'escalate', strategy?: str, escalation_advice?: str[]}`
  - `pipeline.py`：`_PhaseError` 捕获 → 先调 `_attempt_self_heal(cr)` → action=retry 重起 pipeline（attempt 计数 ≤ 2）；action=escalate 或 attempt 用完 → append 一条 type=ai message 到 conversation：「我试了 X、Y 都没成功，需要你协助：建议 1) ... 2) ...」
  - `ChangeRequest` 加 `self_heal_attempts: int` 列（Task 1 已加 schema 里就追加这列）
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 5b — pipeline 失败自愈 + escalate 业务员`

### Task 6 — dev_runner refine 模式 prompt

- [ ] **Step 1 RED**：`tests/test_dev_runner_refine_prompt.py`
  - `test_opencode_runner_refine_prompt_includes_previous_diff`
  - `test_opencode_runner_refine_prompt_lists_chat_history`
  - `test_opencode_runner_refine_prompt_omits_full_entry_files`（已经知道，不重复贴）
  - `test_claude_code_runner_refine_same_behavior`
- [ ] **Step 2 实现**：
  - `opencode_runner.py` + `claude_code_runner.py` 的 `build_prompt(ctx)` 根据 `ctx.mode` 分支：
    - `new_cr` 走现状
    - `refine` 改成「你已经在分支 X 上做过一轮改动，业务员追加反馈：'...'；之前的 chat 历史：... ；请继续在同分支改」
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 6 — dev runner refine prompt`

### Task 7 — preview_adapter 同 handle 覆盖（refine 不起新容器）

- [ ] **Step 1 RED**：`tests/test_preview_refine_reuse.py`
  - `test_docker_preview_refine_reuses_same_handle`
  - `test_docker_preview_refine_keeps_same_port`
  - `test_docker_preview_new_cr_uses_new_port_as_before`
- [ ] **Step 2 实现**：
  - `docker_preview.py`：`serve(repo, branch, *, base_handle=None)`：base_handle 非空时 `docker compose restart` 而非 `up`；端口复用上 CR；container name 同 base
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 7 — preview 复用 handle`

### Task 8 — orchestrator BrainstormingSkill 真多轮 + options

- [ ] **Step 1 RED**：`tests/test_multiturn_clarify.py`
  - `test_clarify_loops_until_llm_returns_done_true`
  - `test_clarify_each_round_passes_previous_answers_in_prompt`
  - `test_clarify_each_round_passes_screenshot_for_vision`
  - `test_clarify_soft_cap_8_rounds_force_break`
  - `test_clarify_user_skip_all_breaks_loop`（answer == `__STOP_CLARIFY__`）
  - `test_clarify_options_passed_to_channel_ask`
  - `test_clarify_heavy_path_still_uses_variants_no_loop`
  - `test_clarify_no_questions_skips_clarify_entirely`
- [ ] **Step 2 实现**：
  - `brainstorming_skill.py` 重写 `clarify()`：
    - 第一轮 `_plan_initial`：判 weight，heavy → variants 不变；light → 进 loop
    - `for round_i in range(MAX_SOFT_ROUNDS=8):`
      - 调 `_plan_next(prev_answers)` 返 `{done, question, options[]}`
      - `done=True` → break
      - `channel.ask(question, options)` 拿 answer
      - `answer == '__STOP_CLARIFY__'` → break
      - 否则 append 到 clarifications
    - 返 `RequestBrief(clarifications=...)`
  - prompt：「**强制返 2-4 个 options**，最后一个 option 必须是 "我自己描述"；判 done=True 仅当业务员明确了所有歧义点」
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 8 — 真多轮澄清 + 强制 options`

### Task 9 — orchestrator multi-image vision support

- [ ] **Step 1 RED**：`tests/test_multi_image_vision.py`
  - `test_clarify_with_3_attachments_sends_all_to_vision_model`
  - `test_vision_prompt_orders_images_by_attachment_index`
  - `test_legacy_single_screenshot_b64_still_works`
- [ ] **Step 2 实现**：
  - `_llm.py` 加 `complete_vision_multi(prompt, image_b64_list)`；OpenAI-compatible API 的 content 数组支持 N 张 image_url
  - BrainstormingSkill 拿 `raw.attachments` 而不是单 `raw.screenshot_b64`，全部塞进 vision
  - `RawRequest` 加 `attachments: list[Attachment]`（schemas 同步）；老 `screenshot_b64` 单字段保留兜底转一个 attachment
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 9 — 多图 vision`

### Task 10 — orchestrator /messages 端点 + REST 改造

- [ ] **Step 1 RED**：`tests/test_messages_api.py`
  - `test_post_messages_routes_to_new_cr_when_attachment`
  - `test_post_messages_routes_to_refine_when_no_attachment_after_preview`
  - `test_post_messages_routes_to_chat_only_when_no_attachment_after_failed`
  - `test_post_messages_returns_message_id_plus_cr_id_when_applicable`
  - `test_post_messages_persists_user_message_before_dispatch`
- [ ] **Step 2 实现**：
  - `main.py` 新加 `POST /conversations/{conv_id}/messages`：
    - body: `{ text, attachments?: Attachment[], override_mode? }`
    - flow：append user message → classify intent → 按 mode 触发 (a) `repo.create(...)` + `pipeline.run(mode='new_cr')`；(b) `pipeline.run(mode='refine_cr', refine_of=...)`；(c) `chat_responder.respond(...)`
    - return: `{ message_id, cr_id?: str, mode }`
  - 老 `POST /change-requests` 保留兼容（v0.5 客户端还在用）但 deprecate notice
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(orchestrator): Plan 10 Task 10 — /messages 端点 + 意图路由`

### Task 11 — extension 数据层 types + tabs.ts + attachments.ts

- [ ] **Step 1 RED**：
  - `tests/tabs-storage.test.ts`：openTabIds CRUD / activeTabId 切换 / LRU evict on overflow (>8) / 持久化
  - `tests/attachments.test.ts`：framed_region 构造 / pasted_image multi / 兼容老 single screenshot
- [ ] **Step 2 实现**：
  - `lib/types.ts`：`Message`、`Attachment`、`ConversationTab`、`IntentMode`
  - `lib/tabs.ts`：`loadTabs`、`saveTabs`、`openTab`、`closeTab`、`setActiveTab`、`evictLRU`
  - `lib/attachments.ts`：`collectFramedRegion`、`pasteImage`、`attachFile`
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 11 — tabs + attachments 数据层`

### Task 12 — extension client intent classifier mirror

- [ ] **Step 1 RED**：`tests/intent-classifier-client.test.ts`
  - 跟 server `test_intent_classifier.py` 同 6 case 镜像（保证 client/server 决策一致）
- [ ] **Step 2 实现**：
  - `lib/intent.ts`：`classifyIntent(message, attachments, conversationMessages, lastCrState) → IntentMode`
  - SW 在 SUBMIT 路径调它决定 UX hint，但**最终决策仍以 server 为准**（server `POST /messages` 自己再 classify 一次）
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 12 — client intent classifier mirror`

### Task 13 — extension SW 改造支持 /messages

- [ ] **Step 1 RED**：扩展 `service-worker-multi.test.ts` 加：
  - `test_submit_with_no_attachment_calls_messages_endpoint`
  - `test_submit_with_attachments_calls_messages_with_array`
  - `test_chat_only_response_no_new_mirror_added`
  - `test_refine_response_updates_existing_mirror`
- [ ] **Step 2 实现**：
  - `orchestrator-client.ts`：`postMessage(convId, body)`
  - `service-worker.ts`：旧 SUBMIT_TEXT_ONLY / CONFIRM_CAPTURE 重定向到 `client.postMessage(...)`；附件列表从 `lib/attachments.ts` 收集
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 13 — SW 路由到 /messages`

### Task 14 — extension AgentTabBar + HistoryDropdown

- [ ] **Step 1 RED**：`tests/agent-tab-bar.test.tsx`
  - 渲染 N 个 tab + active 高亮
  - 点 `+` 调 createConversation 并 setActive
  - 点 `×` 关闭 tab（不删 conversation）
  - 点 `🕐` 出 dropdown + Search 过滤 + Archived 折叠
  - tab > 8 LRU evict 最老
- [ ] **Step 2 实现**：
  - `components/AgentTabBar.tsx`：横向 tab 行 + `+` + `🕐`
  - `components/HistoryDropdown.tsx`：列 `listConversations` + Search input + Archived 折叠 + 选中 → openTab(id) + setActive
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 14 — AgentTabBar + HistoryDropdown`

### Task 15 — extension ChatStream + InlineCards

- [ ] **Step 1 RED**：`tests/chat-stream.test.tsx`
  - 渲染 user/ai/summary message 气泡
  - user message 带 attachments 显缩略图 grid
  - 关联 CR 状态徽章渲染（preview-ready → 浅绿 chip）
  - inline ClarifyCard / VariantCard / PreviewCard / FailCard 在相应 cr 状态时插入流末尾
  - Plan 9 CompactedRangeNotice 仍然 work
- [ ] **Step 2 实现**：
  - `components/ChatStream.tsx`：拉 `getConversation(id).messages` + 当前 conversation 的 active mirror，按 ts 排序
  - `components/ChatBubble.tsx`：单条 message
  - `components/InlineCards/*`：把 Plan 9 时代的 ClarifyPanel/VariantsPanel/FailedPanel/PreviewPanel 改造成可内嵌的卡片
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 15 — ChatStream + Inline cards`

### Task 16 — extension ChatInputBar 加 AttachmentTray + mode 切换

- [ ] **Step 1 RED**：`tests/chat-input-bar-attachments.test.tsx`
  - 附件 tray 显示已附图 + × 删
  - 点「+ 框选」起 framed_region 流程；review 后回 attachment tray
  - 点「📎 贴图」打开 file picker（mock）
  - 拖拽图片到输入框 → push attachment tray
  - mode 切换开关：自动 / 强制 new_cr / 强制 refine
  - ⇧⌘↵ 强制 new_cr 提交
- [ ] **Step 2 实现**：
  - `components/AttachmentTray.tsx`：缩略图 grid + 删
  - 改 `ChatInputBar.tsx`：上方挂 AttachmentTray + 工具栏（+ 框选 / 📎 贴图 / mode toggle）；提交时把 tray 内容打包成 attachments[] 发给 SW
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 16 — ChatInputBar 多附件 + mode 切换`

### Task 17 — extension MainShell 重布局 + 接通 chat 流

- [ ] **Step 1 RED**：integration test
  - 渲染 App → 装 Tab → 在 chat 流提交无附件文本 → 验证调 /messages with no attachment → 收到 chat_only response → 流末尾出现 ai 气泡
  - 提交带附件 → /messages with attachments → 出 InlineClarifyCard 气泡
- [ ] **Step 2 实现**：
  - `App.tsx`：MainShell 改成 `<AgentTabBar /> + <ChatStream /> + <footer><ChatInputBar /></footer>`
  - 移除老 PreviewDock（功能并入 InlinePreviewCard）；Plan 9 的 ChatInputBar / PreviewDock 设计在这步合并升级
- [ ] **Step 3 GREEN**
- [ ] **Step 4 提交**：`feat(extension): Plan 10 Task 17 — MainShell 重布局接通 chat 流`

### Task 18 — E2E + 文档 + v0.6.0

- [ ] **Step 1 ECS deploy** —— `bash deploy/deploy.sh`（含自动备份）
- [ ] **Step 2 端到端 happy path**：
  - 新建 tab → 框选 + 提交「订单徽章改红」→ AI 真多轮澄清 2-3 题（带 options）→ business 选了 + 答了一题自定义 → preview-ready → InlinePreviewCard 出现
  - 不附图直接打字「字号大一点」→ refine 路径 → 同 branch 改 → preview 容器原地刷新 → 同 url 内容更新
  - 不附图打字「你觉得这次改的怎么样？」→ chat_only 路径 → AI 回复气泡（不进 docker 不切 branch）
  - 关 tab → 重开 → 切回历史 conversation → chat 流完整 + 输入框继续
  - 开 8 个 tab 测 LRU evict
- [ ] **Step 3 真人 E2E**：5 人各跑一遍；成功率 ≥ 80%；卡点回这里调 prompt / UX
- [ ] **Step 4 文档**：
  - `docs/CONVERSATION-MODEL.md`：四概念图（conversation = tab；message = chat 流条目；CR = 触发 pipeline 的 message；附件 = 视觉锚点）
  - `docs/PLAN10-INTENT-CLASSIFIER.md`：决策表
  - `README.md` 更新「Cursor 式 Agent Tab + 连续对话」
  - `CHANGELOG.md` v0.6.0 条目
- [ ] **Step 5 tag**：`v0.6.0`
- [ ] **Step 6 提交**：`test(extension+orchestrator): Plan 10 E2E + 文档 + v0.6.0`

---

## 验收标准（Plan 10 完成定义）

- [ ] 顶部 tab bar：可以同时开 N (≤8) 个 conversation；点 `+` 新建；点 `×` 关 tab；点 🕐 选历史；超过 8 个 LRU evict
- [ ] 同一 tab 内输入「字号再大一点」**不再起新 CR**，而是 refine 路径：复用上 CR 的 branch + 同 preview 端口刷新
- [ ] 同一 tab 内输入「你觉得这次改的怎么样？」走 chat_only 路径：AI 文字回复 append 到流，不切 branch / 不起 docker / 不耗 quota
- [ ] 输入框附件 tray：可一次贴 3 张图，全部送给视觉模型；多图模式 LLM 拿到所有图作判断
- [ ] 真多轮澄清：LLM 可连续问 2-5 题（按需要），每题强制 2-4 options + 自定义答案；业务员可主动「✓ 够了直接干」终止；软上限 8 题强制 break
- [ ] chat 流主体显示 user/ai message 气泡 + 内嵌 ClarifyCard / VariantCard / PreviewCard / FailCard；ChatInputBar sticky 底部
- [ ] 关浏览器、3 天后再开 → tab 还在；切回历史 conversation → chat 流完整
- [ ] 没附件 + 第一次说话 → 自动 chat_only；带附件 → 自动 new_cr；按规则可被业务员强制 override
- [ ] v0.6.0 tag 推 origin，CHANGELOG + CONVERSATION-MODEL + INTENT-CLASSIFIER 三文档完整

---

## 关键不做（明确不在本 plan 范围）

- **并行 dev runner**（Cursor Agent 3.0 多 worktree 同时跑）—— Plan 8 已有 worktree 隔离基础但 UI 不暴露并行；本 plan 仍是「同 tab 同一时刻一个活跃 CR」
- **checkpoint 回滚**（Cursor "revert to checkpoint" 功能）—— git 层支持但 UI 不做，业务员手动用「丢弃」即可
- **@-mention 跨 conversation 引用**（Cursor `@Past Chats`）—— 数据已 ready 但 UX 难做，留到 Plan 11
- **/commands**（Cursor `/summarize`, `/init`）—— Plan 9 已有 compaction 自动跑，不需要业务员手动触发
- **语音输入** —— 麦克风 icon UI 视觉占位即可，不接 Web Speech API
- **PDF / 大文件 / 跨文件 attach** —— 数据契约支持（kind=attached_file），但 Plan 10 UI 只暴露图片 + 框选；PDF 留 Plan 11
- **跨设备同步 conversation** —— 沿用 Plan 9 决策：chrome.storage.local 本地、server 同 admin_token 即可
- **修改 max_questions=3 配置** —— 直接物理删除该参数，所有 callers 改为 MAX_SOFT_ROUNDS
- **业务员可看 / 编辑老 message 文本**—— append-only

## 风险 + 缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| refine 模式 dev_runner 没接续上一 branch 的能力 | refine 路径跑挂 | Task 6 单独花时间调 opencode / claude-code 的 "continue session" prompt；最坏降级为 new_cr 走全 pipeline |
| 多轮澄清 LLM cost 飙升 | 单 CR 烧 5-10 次 vision call | 软上限 8 + 业务员主动跳出 + Plan 9 compaction 缓解上下文累积 |
| intent_classifier 误判 | 业务员要新 CR 但系统判 refine（或反之）| (1) UI 显示「即将走 refine / new_cr / chat_only」预览；(2) 业务员可手动 override |
| chat_only 路径 LLM 出错答非业务问题 | 答得离谱 | prompt 严约束「不要写代码、不要承诺改东西」+ 业务员说「那你去改吧」时下次 message 走 new_cr |
| tab > 8 LRU evict 误删业务员当前的 tab | UX 翻车 | LRU 排除 activeTabId + 最近 1 小时活跃的 tab；只 evict 真闲置的 |
| docker_preview refine 复用同 handle 出错 | refine preview 显示老内容 | Task 7 写 contract test 验真的 restart；失败降级为新起容器 |
| schema migration 新加 3 列在生产卡住 | 启动失败 | 复用 Plan 9 同套幂等 ADD COLUMN + warning fallback；rollback.sh 不动 DB 仍 safe |

## 需要用户提供（运行 Plan 10 前的一次性清单）

- 确认 ECS 仍是 v0.5.0 部署（已知，跳过）
- 如果要测多图：业务员准备 2-3 张参考截图（任意 PNG/JPEG）
- 真人 E2E 5 人名单（Plan 10 Task 18 Step 3）
- 是否接受 LLM cost 翻 3-5x（多轮 + 多图）—— 已知用户回答「不在乎时间和 token」

## 与 v0.5.0 的关系

Plan 9 留了 conversation + messages 模型 + compaction 框架，本 plan 在它之上叠加：

- v0.5.0 `messages` 只存 ai 完成总结 + summary → Plan 10 改为存所有 user + ai + summary + system
- v0.5.0 客户端没传 conversation_id 每条新建 → Plan 10 tab 模型显式绑 conversation
- v0.5.0 一条 CR = 一条 user input → Plan 10 一条 message 才是 user input；CR 仅 new_cr/refine_cr 时产生
- v0.5.0 PreviewDock 底部浮卡 → Plan 10 InlinePreviewCard 内嵌气泡
- v0.5.0 max_questions=3 单轮 → Plan 10 真多轮 + 强制 options

Plan 9 的 ChatInputBar 不会被替换，会被升级（加附件 tray + mode 切换）。

---

## 估算

按任务粒度合计（含 TDD 测试 + commit 时间）：

| 范围 | 工作日 |
|---|---|
| orchestrator backend（Task 1-10） | 5-6 |
| extension frontend（Task 11-17） | 4-5 |
| E2E + 文档 + 真人验证（Task 18） | 1-2 |
| **总计** | **10-13 天** |

按用户要求「不在乎时间和 token」，不做时间压缩；走完整 TDD + Plan 9 同等密度的 code review + 测试覆盖。
