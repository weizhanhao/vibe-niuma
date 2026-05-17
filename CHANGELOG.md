# Changelog

doskill 项目版本记录。语义化版本（major.minor.patch），主要面向业务员看到的能力变化。

## [v0.6.0] — 2026-05-17

**主题：Cursor 式 Agent Tab + 多附件 + 真多轮澄清 + 意图路由**

让扩展真正像 Cursor 工作：顶部多 tab、底部 sticky 输入框、AI 主动判断「这是新需求 / 续改 / 闲聊」分别走不同处理路径，澄清没有固定轮数限制。

### Added

- **POST `/conversations/{conv_id}/messages` 统一入口**（orchestrator）：业务员每条 message 进这里，server 用 LLM 分类决定走 new_cr / refine_cr / chat_only 三条路径之一。返 `{message_id, mode, cr_id?, ai_message_id?, confidence, is_unsure, reason}`。`is_unsure` 用于 UI 提示业务员手动确认。
- **IntentClassifier**（orchestrator + extension mirror）：基于 LLM 的意图分类器，看消息 + 最近 6 条历史 + 上一 CR state 判 mode。客户端用启发式 mirror 给即时 UX hint，server 仍以 LLM 决策为准。
- **ChatResponder（chat_only 路径）**：业务员说「这个改得怎么样」「为啥用这种方案」时不进 pipeline，AI 直接文字回复并 append 到 conversation。
- **多附件（≤3 张）**：`Attachment` schema + `attachments` JSON 列 + `LLMClient.complete_vision_multi*`。业务员一次 message 可附 0-3 张图（framed_region / pasted_image / screenshot_active_tab / attached_file）；vision API 一次塞多图判意图。
- **真多轮澄清**（BrainstormingSkill 重写）：去掉 `max_questions=3` 硬上限，改 `MAX_SOFT_ROUNDS=8` 软上限。每轮 LLM 重判「done? 下一题？」，业务员可点「✓ 够了直接干」`STOP_CLARIFY_SENTINEL` 主动结束。每个问题必带 2-4 个 options，末位固定「我自己描述」。
- **refine_cr 路径**：识别「字号大一点」「再深点」这类追加修饰，pipeline 在 base CR 的 branch 上续 commit + Vite 热重载复用同预览容器，秒级反馈。
- **self_heal 自愈**：CR 失败后 LLM 决定 retry / retry_with_revised_prompt / escalate（最多 2 次），自动恢复网络抖动或 transient build 错。
- **AgentTabBar + HistoryDropdown**（extension）：顶部多 tab（每 tab 一会话，× 关闭，+ 新建，🕐 历史下拉）。LRU at 8 tabs。
- **ChatStream + InlineCard**（extension）：主体是消息流，user message 下方挂 CR 状态卡 + 预览链接；summary 渲染为折叠条。
- **AttachmentTray + mode badge**（extension）：输入框上方实时显示模式徽章；附件 chip 可移除；满 3 张「+」禁用。

### Changed

- `BrainstormingSkill` 问答返 JSON shape：从 `{questions:[...]}` 改为每轮 `{done, question, options, variants?}`。
- `RawRequest` 加 `attachments: list[dict]`；`images()` 方法返 vision-ready 图列表（兼容老 single screenshot_b64）。
- `Pipeline.run(request_id, *, mode)` 三分支调度。
- `Pipeline.run_chat_only(...)` 不进配额、不切 branch、不起 docker。
- MainShell 重布局：顶部 AgentTabBar，主体 ChatStream，底部 ChatInputBar；ConversationList 被替代。

### Removed

- 老 POST `/conversations/{id}/messages` 裸 append 入口（被新的意图路由入口取代；端到端覆盖见 `test_messages_api.py`）。
- `BrainstormingSkill.max_questions` 硬上限。

### Dependencies

无新增。

### Migration

- `change_request` 表新加列：`attachments JSON / mode VARCHAR(16) / refine_of VARCHAR(36) / self_heal_attempts INT`。
- ECS 上靠 `_ensure_schema_migrations` 在 lifespan 自动 ALTER；零运维。
- v0.5 客户端继续可用：老 POST `/change-requests` 路径保留，`screenshot_b64` 单字段自动转 Attachment。

### 测试

- orchestrator：333 passed / 5 skipped（含 3 个 Plan 10 obsolete legacy contract）
- extension：283 passed / 4 skipped；vite build 通过

---

## [v0.5.0] — 2026-05-16

**主题：多项目 + Cursor 式持续对话 + 动态压缩**

把扩展从「一次性窗口」升级成「Cursor 式持续对话工作台」。三个绑在一起的能力：

### Added

- **多项目模型**（extension）：扩展不再绑死单个项目，业务员可以在 head 下拉里切换 `demofrontend` / `demobackend` / `xxx-saas` 等独立项目，每个项目自带 orchestrator 地址 / token / 模型配置。`doskill_projects[]` + `doskill_active_project_id` 存 chrome.storage。setActiveProject 同步 `doskill_config_v2` 让 service-worker 不必改读路径。
- **Conversation 持久对话**（orchestrator）：DB 加 `conversation` 表 + `change_request.conversation_id` 外键。同一 conversation 里 N 个 CR 共享 chat history，业务员说完「订单徽章改红」AI 做完，可以继续说「字号大一点」，AI 看到完整历史接着改。`/conversations` REST CRUD + 自动 bucket 老 CR 到 Legacy conversation。
- **动态压缩**（orchestrator）：`compaction.estimate_tokens` 用 tiktoken cl100k_base 估对话 token；超 40k 触发 LLM 把老 ai 消息压成中文摘要，user 消息 / 最近 6 轮 / 标 `preserve=True` 的全保留。pipeline 在 dev_runner 启动前自动调用，新 summary 写回 conversation 表，下轮直接命中。
- **PreviewDock 底部浮卡**（extension）：preview-ready / merged / discarded 时主体不再阻塞，业务员可以继续打字起新 CR；最近一条带 preview_url 的 CR 用底部 dock 展示（branch chip + URL + ↗ 打开 + 丢弃 + 合并）。
- **CompactedRangeNotice**（extension）：chat 里遇 type=summary 渲染为「已折叠 47 条历史（~12.3k tokens）▾」折叠条，按需展开 drawer 看完整老消息。
- **ProjectSwitcher / ProjectSelectorPanel / CreateProjectPanel**（extension）：head 下拉切项目；首次安装走全屏选择器；新项目沿用 Plan 7 部署助手 + 默认值修补流程。

### Changed

- MainShell 路由：preview-ready / merged / discarded 时 body 自动切回 CapturePanel（继续对话），PreviewPanel 不再阻塞主体。
- `/change-requests` POST 自动给没传 `conversation_id` 的请求建一个新 conversation。
- 启动时 `_bucket_legacy_crs` 把所有 `conversation_id IS NULL` 的老 CR 关到一条 `Legacy` conversation 下，保留历史可见性。

### Fixed

- `test_models` 列名集合补齐 `repos`（Plan 8）+ `conversation_id`（Plan 9）。
- `test_lifespan` 用原生 SQL 绕开 detached ORM instance 的 lazy-load `DetachedInstanceError`（lifespan recovery 会 close 复用的 db_session）。

### Dependencies

- 新增：`tiktoken>=0.7`（compaction 用）

---

## [v0.4.0] — 2026-05-15

**主题：多仓项目支持 + 实时日志**

- Plan 8 全 12 task：multi_repo discover / merge_to_main_atomic / dev_runner 多仓 commit / react_vite 多仓 build / pipeline 整合
- Plan F：SSE 流式 log 各阶段实时回推扩展

## [v0.3.0] — 2026-05-14

Plan 7 部署助手 + 多 CR sidebar。

## [v0.2.0] — 2026-05-13

Plan 6 配置向导（4 步 wizard）+ 设置面板。

## [v0.1.0-mvp] — 2026-05-12

Plan 1~5 MVP：orchestrator 骨架 / dev runner / 浏览器扩展 / E2E 集成 / ECS 部署。
