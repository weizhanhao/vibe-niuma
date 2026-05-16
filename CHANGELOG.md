# Changelog

doskill 项目版本记录。语义化版本（major.minor.patch），主要面向业务员看到的能力变化。

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
