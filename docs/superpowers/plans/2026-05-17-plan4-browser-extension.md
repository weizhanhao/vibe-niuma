# Plan 4 — 浏览器扩展（捕获 + 澄清/选方案 UI + 状态）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个 Chrome 浏览器扩展（Manifest V3）：业务员在 demo 页面上框选区域、用自然语言说需求；扩展捕获 URL + 高亮框截图 + 坐标 + viewport，POST 给 Orchestrator，订阅 SSE 展示状态机变迁，渲染澄清问答 / HTML 方案选择，到 `preview-ready` 后能开预览、点确认合并或丢弃。

**Architecture:** Manifest V3 扩展，三部分：① **content script** 注入到页面，负责框选 overlay + 坐标采集；② **background service worker** 负责截图（`chrome.tabs.captureVisibleTab`）、与 Orchestrator 的 REST + SSE 通信、状态机镜像；③ **side panel UI** 负责需求输入、澄清问答、方案选择、状态展示、预览/合并/丢弃操作。扩展是薄客户端 —— 不含业务逻辑，只是 Orchestrator 状态机的观察者 + 输入采集器（设计文档 §3.1）。所有面板在扩展内，单角色自审（设计文档 §2）。

**Tech Stack:** TypeScript + Vite（`@crxjs/vite-plugin`）；React 用于 UI（与 demo 前端同栈，复用 web 规则）；`EventSource` 订阅 SSE；vitest + `@testing-library/react` 做组件/集成测试。

---

## 前置约定（每个任务都假定已满足）

- 新目录 `extension/`，独立 `package.json` + Vite 构建；不与 `demo/frontend` 或 `orchestrator/` 混。
- npm 走 `registry.npmmirror.com`（沿用项目既定，`.npmrc`）。
- Orchestrator 地址是配置项（开发指向 `http://localhost:9000`，ECS 上指向 ECS 地址）；扩展里用 `chrome.storage` 存。
- 本计划在新分支 `plan4-browser-extension` 上做。
- Orchestrator 的 REST/SSE 契约见 Plan 2 的 `orchestrator/README.md`：`POST /change-requests`、`GET /change-requests/{id}`、`GET /change-requests/{id}/events`（SSE，事件 `status`/`question`/`variants`）、`POST /change-requests/{id}/answer`、`POST .../merge`、`POST .../discard`、`POST .../retry`。
- 安全不考虑（设计文档 §2）。
- UI 遵循用户的 web design-quality 规则：不做模板感 UI，有意图、有层级、有交互态。

## File Structure

```
extension/
  package.json
  vite.config.ts
  .npmrc
  manifest.json                 # MV3 manifest
  tsconfig.json
  src/
    content/
      capture-overlay.ts        # 框选 overlay：拖拽画框、采集 boxCoords + viewport
      content-entry.ts          # content script 入口，监听 background 的「开始框选」消息
      overlay.css
    background/
      service-worker.ts         # background 入口：截图、REST、SSE、消息路由
      orchestrator-client.ts    # Orchestrator REST + SSE 封装
      request-store.ts          # 当前变更请求的状态镜像（chrome.storage 持久化）
    ui/
      App.tsx                   # side panel 根组件：路由到各阶段视图
      panels/
        CapturePanel.tsx        # 需求输入 + 「开始框选」按钮
        ClarifyPanel.tsx        # 澄清问答（文字问题）
        VariantsPanel.tsx       # HTML 方案选择
        StatusPanel.tsx         # 状态机进度展示
        PreviewPanel.tsx        # preview-ready：开预览 + 合并/丢弃
        FailedPanel.tsx         # 失败展示 + 重试
        SettingsPanel.tsx       # Orchestrator 地址设置
      components/
        ProgressTrail.tsx       # FSM 阶段进度条
        Button.tsx, Card.tsx    # 基础 UI（遵循 design-quality 规则）
      hooks/
        useRequestState.ts      # 订阅 background 的状态镜像
      ui-entry.tsx              # side panel 入口
      tokens.css                # 设计 token（颜色/字号/间距/动效）
    lib/
      types.ts                  # 与 Orchestrator 对齐的 TS 类型
      messages.ts               # content ↔ background ↔ ui 的消息协议类型
  tests/
    capture-overlay.test.ts
    content-entry.test.ts
    orchestrator-client.test.ts
    request-store.test.ts
    service-worker.test.ts
    panels.test.tsx
    integration.test.tsx
```

---

## Task 1: 扩展骨架与构建

**Files:**
- Create: `extension/package.json`, `extension/vite.config.ts`, `extension/.npmrc`, `extension/manifest.json`, `extension/tsconfig.json`
- Create: `extension/src/lib/types.ts`, `extension/src/lib/messages.ts`

- [ ] **Step 1** — `package.json`：依赖 React、vite、`@crxjs/vite-plugin`、typescript、vitest、`@testing-library/react`、`@testing-library/jest-dom`、jsdom。`.npmrc` 指 npmmirror。
- [ ] **Step 2** — `manifest.json`（MV3）：`permissions`: `activeTab`, `scripting`, `storage`, `tabs`, `sidePanel`；`host_permissions` 指向 demo 地址 + Orchestrator 地址（开发期 `<all_urls>` 也可，安全不考虑）；声明 content script、background service worker、side panel。
- [ ] **Step 3** — `vite.config.ts` 用 `@crxjs/vite-plugin` 打 MV3。`tsconfig.json` 沿用 demo 前端的严格度。
- [ ] **Step 4** — `lib/types.ts`：与 Orchestrator 对齐的类型 —— `ChangeRequestState`（联合字面量，对齐 FSM）、`ChangeRequestOut`、`SSEEvent`（`status`/`question`/`variants` 三种 `data` 形态）、`RawRequestPayload`。`lib/messages.ts`：content↔background↔ui 消息协议（`START_CAPTURE`、`CAPTURE_RESULT`、`REQUEST_STATE_CHANGED`、`SUBMIT_ANSWER`、`MERGE`、`DISCARD`、`RETRY` 等），全部带 TS 类型。
- [ ] **Step 5** — `npm install && npm run build` 能产出可加载的 `dist/`。
- [ ] **Step 6: 提交** — `git commit -m "feat: 扩展骨架与 MV3 构建"`

---

## Task 2: 框选 overlay（坐标采集）

**Files:**
- Create: `extension/src/content/capture-overlay.ts`, `extension/src/content/overlay.css`
- Test: `extension/tests/capture-overlay.test.ts`

- [ ] **Step 1: 写失败测试** — `capture-overlay.test.ts`（vitest + jsdom）：
  - `test_drag_produces_box_coords` — 模拟 mousedown→mousemove→mouseup，得到 `{x, y, width, height}`，坐标相对页面、归一到 CSS 像素。
  - `test_box_normalizes_negative_drag` — 反向拖拽（从右下到左上）也得到正的 width/height。
  - `test_capture_collects_viewport` — 采集 `{width, height}` viewport（`window.innerWidth/Height`）。
  - `test_escape_cancels_capture` — 按 Esc 取消，不产出坐标。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `capture-overlay.ts`** — 一个 `CaptureOverlay` 类：`start()` 注入一个半透明全屏 overlay + 可拖拽选框；`mousedown/move/up` 画框；归一化坐标；`Esc` 取消；完成时 resolve `{boxCoords, viewport}`。`overlay.css` 用 token 化样式（遵循 web coding-style：CSS 自定义属性，动效只用 transform/opacity）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: 框选 overlay 与坐标采集"`

---

## Task 3: content script 入口

**Files:**
- Create: `extension/src/content/content-entry.ts`
- Test: `extension/tests/content-entry.test.ts`

- [ ] **Step 1: 写失败测试** — mock `chrome.runtime`：`test_start_capture_message_triggers_overlay`（收到 `START_CAPTURE` 启动 overlay）、`test_capture_result_posted_back`（overlay 完成后把 `CAPTURE_RESULT`（含 URL `location.href` + boxCoords + viewport）发回 background）、`test_escape_sends_cancel`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `content-entry.ts`** — 监听 `chrome.runtime.onMessage`，`START_CAPTURE` → `new CaptureOverlay().start()` → 把结果（含 `window.location.href`）`chrome.runtime.sendMessage(CAPTURE_RESULT)`。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: content script 入口"`

---

## Task 4: Orchestrator 客户端（REST）

**Files:**
- Create: `extension/src/background/orchestrator-client.ts`
- Test: `extension/tests/orchestrator-client.test.ts`

- [ ] **Step 1: 写失败测试** — mock `fetch`：
  - `test_create_change_request_posts_payload` — `createChangeRequest(payload)` POST 到 `<base>/change-requests`，body 是 `{url, screenshot_b64, box_coords, viewport, request_text}`，返回 `ChangeRequestOut`。
  - `test_get_change_request` — `getChangeRequest(id)` GET 正确路径。
  - `test_submit_answer` / `test_merge` / `test_discard` / `test_retry` — 各打对应端点。
  - `test_base_url_from_storage` — base url 从 `chrome.storage` 读，缺省 `http://localhost:9000`。
  - `test_http_error_throws` — 非 2xx 抛带状态码的错误。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `orchestrator-client.ts`** — `OrchestratorClient`：构造时从 `chrome.storage` 取 base url；方法 `createChangeRequest`、`getChangeRequest`、`submitAnswer`、`merge`、`discard`、`retry`。纯 `fetch`，统一错误处理。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: Orchestrator REST 客户端"`

---

## Task 5: Orchestrator 客户端（SSE）

**Files:**
- Modify: `extension/src/background/orchestrator-client.ts`
- Test: `extension/tests/orchestrator-client.test.ts`（追加）

- [ ] **Step 1: 追加失败测试** — mock `EventSource`：
  - `test_subscribe_events_dispatches_status` — `subscribeEvents(id, handler)` 收到 `status` 事件，`handler` 拿到解析后的 `{type:"status", data}`。
  - `test_subscribe_events_dispatches_question_and_variants` — `question` / `variants` 事件同样分发。
  - `test_subscribe_returns_unsubscribe` — 返回的函数能关掉 `EventSource`。
  - `test_sse_error_triggers_reconnect_and_get` — 连接 error 时按退避重连，并在重连后拉一次 `GET`（设计文档 §5.3：SSE 只是优化，GET 才是真相）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 给 `OrchestratorClient` 加 `subscribeEvents(id, handler) -> unsubscribe`：用 `EventSource` 连 `/change-requests/{id}/events`，`addEventListener` 三种事件类型；error 时指数退避重连 + 重连成功后 `getChangeRequest` 兜底。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: Orchestrator SSE 客户端与重连"`

---

## Task 6: 请求状态镜像

**Files:**
- Create: `extension/src/background/request-store.ts`
- Test: `extension/tests/request-store.test.ts`

- [ ] **Step 1: 写失败测试** — `request-store.test.ts`：
  - `test_apply_status_event_updates_state` — `status` 事件把镜像的 `state` 推进。
  - `test_apply_question_event_sets_pending_question` — `question` 事件把 `pendingQuestion` 设上。
  - `test_apply_variants_event_sets_pending_variants` — 同理。
  - `test_answering_clears_pending` — 提交回答后 `pendingQuestion`/`pendingVariants` 清空。
  - `test_store_persists_to_chrome_storage` — 镜像变化写 `chrome.storage`（扩展重开能恢复）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `request-store.ts`** — 一个纯 reducer + 一层 `chrome.storage` 持久化：`RequestState`（`id`, `state`, `branch`, `previewUrl`, `failPhase/Reason`, `pendingQuestion`, `pendingVariants`），`applyEvent(state, sseEvent) -> state`（不可变更新），`saveToStorage` / `loadFromStorage`。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: 请求状态镜像与持久化"`

---

## Task 7: background service worker（消息编排）

**Files:**
- Create: `extension/src/background/service-worker.ts`
- Test: `extension/tests/service-worker.test.ts`

- [ ] **Step 1: 写失败测试** — mock `chrome.*`、`OrchestratorClient`、`RequestStore`：
  - `test_capture_result_triggers_screenshot_and_create` — 收到 `CAPTURE_RESULT`，调 `chrome.tabs.captureVisibleTab` 截图，组装 payload（含暂存的 `request_text`），调 `createChangeRequest`，订阅 SSE。
  - `test_sse_event_updates_store_and_notifies_ui` — SSE 事件经 store reducer 后，向 UI 广播 `REQUEST_STATE_CHANGED`。
  - `test_submit_answer_message_calls_client` — UI 发 `SUBMIT_ANSWER` → 调 `client.submitAnswer`。
  - `test_merge_discard_retry_messages` — UI 的合并/丢弃/重试消息路由到对应 client 方法。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `service-worker.ts`** — 消息路由中枢：UI 的 `START_CAPTURE` 暂存 `request_text` 并转发给 content script；content 的 `CAPTURE_RESULT` → 截图 + create + subscribe；SSE 事件 → store → 广播给 UI；UI 的操作消息 → client。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: background service worker 消息编排"`

---

## Task 8: UI 骨架 + CapturePanel

**Files:**
- Create: `extension/src/ui/ui-entry.tsx`, `extension/src/ui/App.tsx`, `extension/src/ui/tokens.css`
- Create: `extension/src/ui/hooks/useRequestState.ts`
- Create: `extension/src/ui/components/Button.tsx`, `extension/src/ui/components/Card.tsx`
- Create: `extension/src/ui/panels/CapturePanel.tsx`
- Test: `extension/tests/panels.test.tsx`（CapturePanel 部分）

- [ ] **Step 1: 写失败测试** — `panels.test.tsx`：`test_capture_panel_renders_input_and_button`、`test_capture_panel_start_capture_sends_message`（填需求文本 + 点「开始框选」→ 发 `START_CAPTURE` 带 `request_text`）、`test_capture_panel_requires_non_empty_text`（空文本时按钮禁用）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `tokens.css` 定义设计 token（遵循 web design-quality：有意图的配色 + 字阶 + 间距节奏）。`useRequestState` hook 订阅 background 的 `REQUEST_STATE_CHANGED`。`App.tsx` 按 `RequestState.state` 路由到各 panel（无请求 → CapturePanel）。`CapturePanel`：需求文本输入 + 「开始框选」按钮（空文本禁用），点击发 `START_CAPTURE`。`Button`/`Card` 带 hover/focus/active 态。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: UI 骨架与 CapturePanel"`

---

## Task 9: ClarifyPanel + VariantsPanel

**Files:**
- Create: `extension/src/ui/panels/ClarifyPanel.tsx`, `extension/src/ui/panels/VariantsPanel.tsx`
- Test: `extension/tests/panels.test.tsx`（追加）

- [ ] **Step 1: 追加失败测试**：
  - `test_clarify_panel_renders_question_and_options` — 有 `pendingQuestion` 时渲染问题文本 + 可选项（若有）+ 文本输入 + 「跳过」。
  - `test_clarify_panel_submit_sends_answer` — 回答后发 `SUBMIT_ANSWER`（含 `question_id`）。
  - `test_clarify_panel_skip_sends_empty` — 「跳过」发空答案。
  - `test_variants_panel_renders_html_mockups` — 有 `pendingVariants` 时把每套 `HtmlMockup` 渲染成可预览卡片（`iframe srcdoc` 沙箱渲染）。
  - `test_variants_panel_select_sends_answer` — 选中一套 → 发 `SUBMIT_ANSWER`（答案是 variant id）。
  - `test_variants_panel_reject_all` — 「都不要」选项。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `ClarifyPanel`：渲染文字问题（一次一问）+ 选项按钮 + 自由文本 + 跳过。`VariantsPanel`：把 2-3 套 `HtmlMockup` 用 `iframe[srcdoc]` 并排渲染成卡片，可选中或全否。两者都遵循 design-quality（卡片有层级、选中态明显）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: ClarifyPanel 与 VariantsPanel"`

---

## Task 10: StatusPanel + ProgressTrail

**Files:**
- Create: `extension/src/ui/panels/StatusPanel.tsx`, `extension/src/ui/components/ProgressTrail.tsx`
- Test: `extension/tests/panels.test.tsx`（追加）

- [ ] **Step 1: 追加失败测试**：
  - `test_progress_trail_highlights_current_phase` — 给定 `state`，进度条把当前阶段及之前标为完成/进行中。
  - `test_status_panel_shows_phase_label` — 渲染当前阶段的中文标签（`clarifying`→「澄清中」等）。
  - `test_status_panel_shows_queued_when_created` — `created` 时显示「排队中」。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `ProgressTrail`：FSM 阶段（created→clarifying→located→coding→building→preview-ready）的水平进度条，当前阶段高亮、动效用 transform/opacity。`StatusPanel`：进度条 + 当前阶段中文文案 + 必要的等待说明。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: StatusPanel 与 ProgressTrail"`

---

## Task 11: PreviewPanel + FailedPanel

**Files:**
- Create: `extension/src/ui/panels/PreviewPanel.tsx`, `extension/src/ui/panels/FailedPanel.tsx`
- Test: `extension/tests/panels.test.tsx`（追加）

- [ ] **Step 1: 追加失败测试**：
  - `test_preview_panel_shows_open_link` — `preview-ready` 时显示预览 URL，「打开预览」点击 `chrome.tabs.create`。
  - `test_preview_panel_merge_button` — 「确认合并」发 `MERGE` 消息。
  - `test_preview_panel_discard_button` — 「丢弃」发 `DISCARD` 消息（带二次确认）。
  - `test_preview_panel_shows_merged_state` — `merged` 后显示成功态。
  - `test_failed_panel_shows_phase_reason_log` — `failed` 时显示 phase + reason + 可展开 log。
  - `test_failed_panel_retry_button` — 「重试」发 `RETRY` 消息。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `PreviewPanel`：预览 URL + 打开按钮 + 「确认合并」/「丢弃」（丢弃二次确认）+ `merged`/`discarded` 终态展示。`FailedPanel`：失败 phase 的中文文案 + reason + 可折叠 log + 「重试」。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: PreviewPanel 与 FailedPanel"`

---

## Task 12: 设置页（Orchestrator 地址）

**Files:**
- Create: `extension/src/ui/panels/SettingsPanel.tsx`
- Modify: `extension/src/ui/App.tsx`
- Test: `extension/tests/panels.test.tsx`（追加）

- [ ] **Step 1: 追加失败测试** — `test_settings_panel_saves_base_url`（输入 Orchestrator 地址 → 写 `chrome.storage`）、`test_settings_panel_loads_existing`（已存的地址回显）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — `SettingsPanel`：一个 Orchestrator base URL 输入框 + 保存；`App.tsx` 加入口（齿轮图标）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: 扩展设置页（Orchestrator 地址）"`

---

## Task 13: 端到端联调测试 + README

**Files:**
- Create: `extension/README.md`
- Create: `extension/tests/integration.test.tsx`

- [ ] **Step 1: 写集成测试** — `integration.test.tsx`：用 mock 的 `chrome.*` + mock 的 Orchestrator（`fetch`/`EventSource`），从 CapturePanel 走一遍完整流程：输入需求 → 模拟 `CAPTURE_RESULT` → 经 background → SSE 推 `status`/`question`/`status...`/`preview-ready` → UI 依次切到 Clarify→Status→Preview → 点合并 → merged。断言每步 UI 正确。
- [ ] **Step 2: 运行确认通过**。
- [ ] **Step 3: 写 `README.md`** — 如何 `npm run build`、在 Chrome `chrome://extensions` 加载 `dist/`、设置 Orchestrator 地址、对着本地 demo 跑一遍；列出已知限制（安全不考虑、单用户）。
- [ ] **Step 4: 全量回归** — `npm test` 全绿。
- [ ] **Step 5: 提交** — `git commit -m "feat: 扩展端到端联调测试与 README"`

---

## 验收标准（Plan 4 完成定义）

- [ ] `npm run build` 产出可在 Chrome 加载的 MV3 扩展。
- [ ] 框选 overlay 能采集 URL + boxCoords + viewport；background 能截图并 POST 创建变更请求。
- [ ] SSE 订阅 + 断线重连 + GET 兜底；状态镜像持久化到 `chrome.storage`。
- [ ] 7 个 panel（Capture/Clarify/Variants/Status/Preview/Failed/Settings）全部按 `RequestState` 正确路由与渲染。
- [ ] 澄清问答（可跳过）、HTML 方案选择、开预览、确认合并、丢弃、重试全部可用。
- [ ] UI 遵循 web design-quality 规则，不是模板感 UI。
- [ ] vitest 单测 + 集成测试全绿；`git status` 干净。

---

## 需要用户提供（运行 Plan 4 前的一次性清单）

1. **Orchestrator 地址**：开发期本地（`http://localhost:9000`）即可；若要直接连 ECS，提供 ECS 上 Orchestrator 的公网地址 + 端口。
2. **目标 demo 地址**：扩展要注入到哪个 demo 页面 —— 本地 `http://localhost:5173` 还是 ECS 上的 demo 地址（决定 `manifest.json` 的 `host_permissions`）。
3. **Chrome 版本**：确认用于测试的 Chrome 版本（MV3 + side panel API 需要较新版本）。
4. **side panel vs popup**：UI 用 side panel（更宽、常驻）还是 popup（点图标弹出）？计划默认 side panel，可改。
5. （可选）扩展是否需要图标/品牌素材；MVP 可用占位图标。
