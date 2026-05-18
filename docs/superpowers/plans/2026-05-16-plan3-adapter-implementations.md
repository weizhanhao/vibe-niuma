# Plan 3 — 四个 Adapter 的真实实现 + 契约测试

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Plan 2 里 4 个 fake adapter 换成真实实现（`ReactViteStackAdapter`、`ClaudeCodeDevRunner` + `OpenCodeDevRunner`、`DockerPreviewAdapter`、`BrainstormingSkill`），并为每个 adapter 配一套契约测试，使 Orchestrator 能驱动真实的 demo 仓库。

**Architecture:** 4 个 adapter 仍然实现 Plan 2 已定义的 4 个 `Protocol`（`orchestrator/src/orchestrator/adapters/interfaces.py`），共享类型不变（`adapters/types.py`）。每个真实实现单独成文件放在 `adapters/impl/` 下。`main.py` 的 `AppState.build_pipeline` 是唯一接线改动点 —— 把 fake 换成真实实现，由配置决定用哪个 DevRunner。LLM 驱动的两个 adapter（`BrainstormingSkill`、`*DevRunner`）的契约测试 mock 掉 LLM 调用，只测确定性的胶水逻辑（prompt 组装、子进程编排、diff 判定、轻重路径分流）。

**Tech Stack:** Python 3.11 / FastAPI / 现有 orchestrator 包；`subprocess` 调 `git` / `npm` / `docker` / `claude` / `opencode` CLI；Anthropic-compatible 代理（claude-code-router 或 LiteLLM，二选一，配置项）；`httpx` 用于 `BrainstormingSkill` 的 LLM 调用（经同一代理）；pytest + pytest-asyncio。

---

## 前置约定（每个任务都假定已满足）

- 工作目录 `orchestrator/`，venv 在 `orchestrator/venv/`，已 `pip install -e ".[dev]"`。
- Plan 2 已合并、79 测试通过。本计划在新分支 `plan3-adapter-implementations` 上做。
- demo 仓库在 `<demo_repo_path>`（配置项，本地开发指向 `/Users/weizhanhao/vibe-niuma/demo`，ECS 上指向 ECS 的 demo clone）。
- 本地开发：Docker Desktop 可用；`claude` CLI 已装（`npm i -g @anthropic-ai/claude-code`）；`opencode` CLI 已装。
- 新增配置项写进 `config.py` 的 `Settings` 并在 `.env.example` 列出（见任务 1）。
- LLM 相关测试一律 mock，不真打模型；只有任务 13 的「真实 E2E 冒烟」例外，且默认 `skip`，靠环境变量开启。

## File Structure

```
orchestrator/src/orchestrator/adapters/
  impl/
    __init__.py
    react_vite_stack.py        # ReactViteStackAdapter：解析 RR 路由、URL→源文件、构建
    claude_code_runner.py      # ClaudeCodeDevRunner：调 claude CLI 改代码+commit
    opencode_runner.py         # OpenCodeDevRunner：调 opencode CLI 改代码+commit
    docker_preview.py          # DockerPreviewAdapter：build 镜像 + run 容器 + 端口映射
    brainstorming_skill.py     # BrainstormingSkill：轻/重路径自适应澄清
    _dev_runner_common.py      # 两个 DevRunner 共用：diff 判定、commit、日志收集
    _llm.py                    # LLM 客户端薄封装（经兼容代理），供 BrainstormingSkill 用
orchestrator/src/orchestrator/config.py        # 修改：加 adapter 相关配置项
orchestrator/src/orchestrator/main.py          # 修改：build_pipeline 换真实 adapter
orchestrator/tests/contract/
  __init__.py
  conftest.py                  # 契约测试共用夹具（demo 仓库副本、mock LLM）
  test_react_vite_stack.py     # locate/context_pack/build 契约
  test_docker_preview.py       # 真起容器（标 @pytest.mark.docker）
  test_claude_code_runner.py   # 子进程编排逻辑（mock CLI）
  test_opencode_runner.py      # 子进程编排逻辑（mock CLI）
  test_brainstorming_skill.py  # 轻/重路径分流（mock LLM）
orchestrator/.env.example                      # 修改：列出新配置项
```

---

## Task 1: 扩展配置项

**Files:**
- Modify: `orchestrator/src/orchestrator/config.py`
- Modify: `orchestrator/.env.example`
- Test: `orchestrator/tests/test_config.py`（追加用例）

- [ ] **Step 1: 追加失败测试** — 在 `test_config.py` 加一个用例，断言 `settings` 暴露：`dev_runner`（`"claude-code"|"opencode"`，默认 `"claude-code"`）、`dev_model`（字符串，默认 `"deepseek-chat"`）、`anthropic_base_url`（字符串，默认 `"http://localhost:8787"`）、`llm_api_key`（字符串，默认 `""`）、`vision_model`（字符串，默认 `"qwen-vl-plus"`）、`preview_port_min`/`preview_port_max`（int，默认 `5100`/`5199`）、`docker_network`（字符串，默认 `"bridge"`）、`dev_runner_timeout_seconds`（int，默认 `900`）。
- [ ] **Step 2: 运行确认失败** — `venv/bin/pytest tests/test_config.py -v`，新用例 FAIL。
- [ ] **Step 3: 实现** — 在 `Settings` 里加上述字段（pydantic-settings，环境变量前缀沿用现有约定）。
- [ ] **Step 4: 运行确认通过** — `venv/bin/pytest tests/test_config.py -v`，全绿。
- [ ] **Step 5: 更新 `.env.example`** — 列出全部新字段 + 注释说明（`DEV_RUNNER`、`DEV_MODEL`、`ANTHROPIC_BASE_URL`、`LLM_API_KEY`、`VISION_MODEL`、`PREVIEW_PORT_MIN`、`PREVIEW_PORT_MAX`、`DOCKER_NETWORK`、`DEV_RUNNER_TIMEOUT_SECONDS`）。
- [ ] **Step 6: 提交** — `git commit -m "feat: orchestrator adapter 相关配置项"`

---

## Task 2: ReactViteStackAdapter — locate

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/__init__.py`（空）
- Create: `orchestrator/src/orchestrator/adapters/impl/react_vite_stack.py`
- Create: `orchestrator/tests/contract/__init__.py`（空）
- Create: `orchestrator/tests/contract/conftest.py`
- Test: `orchestrator/tests/contract/test_react_vite_stack.py`

**契约基准：** demo 前端有 4 条路由（见设计文档 §6）：`/` → 看板首页、`/orders` → 订单列表、`/orders/:id` → 订单详情、`/settings` → 设置表单。`locate` 必须对这 4 个 URL path 返回正确的路由组件源文件，动态路由 `/orders/:id` 是难 case。

- [ ] **Step 1: 写 conftest** — `contract/conftest.py` 提供 `demo_repo_copy` 夹具：把真实 `demo/` 目录 `shutil.copytree` 到 `tmp_path`（只复制 `frontend/src` + 必要配置，跳过 `node_modules`），返回路径。这样契约测试针对真实 demo 结构、又不污染源仓库。
- [ ] **Step 2: 写失败测试** — `test_react_vite_stack.py`：
  - `test_locate_static_route_orders` — `locate("http://x/orders")` 的 `LocateResult.entry_files` 含 `frontend/src` 下订单列表页组件文件。
  - `test_locate_root_route` — `locate("http://x/")` 命中看板首页组件。
  - `test_locate_dynamic_route_order_detail` — `locate("http://x/orders/42")` 命中订单详情组件（动态段 `:id` 匹配）。
  - `test_locate_settings_route` — `locate("http://x/settings")` 命中设置表单组件。
  - `test_locate_unmatched_url_returns_empty` — `locate("http://x/no-such-page")` 返回 `entry_files == []`。
- [ ] **Step 3: 运行确认失败**。
- [ ] **Step 4: 实现 `react_vite_stack.py`** — `ReactViteStackAdapter` 实现 `StackAdapter` Protocol。`locate`：定位 demo 的 React Router 路由定义文件（约定 demo 用集中式路由表，如 `frontend/src/routes.tsx` 或 `App.tsx` 里的 `<Routes>`），解析出 `path → 组件文件` 映射；把传入 URL 的 path 按 RR 规则匹配（静态段精确、`:param` 动态段通配、最长匹配优先），返回 `LocateResult(entry_files=[...], route_path=..., is_dynamic=...)`。解析方式 MVP 用正则即可（提取 `<Route path=... element={<X/>} />` + import 语句解析组件文件路径），demo 路由表是已知契约基准。
- [ ] **Step 5: 运行确认通过**。
- [ ] **Step 6: 提交** — `git commit -m "feat: ReactViteStackAdapter locate"`

> **给执行者：** 实现前先读 demo 的实际路由文件（`demo/frontend/src/`），按它真实的写法解析。若 demo 路由写法不利于正则解析，可在本任务内对 demo 路由文件做最小重构（集中成一张显式路由表），但须保持 demo 行为不变并在 commit 里说明。

---

## Task 3: ReactViteStackAdapter — context_pack

**Files:**
- Modify: `orchestrator/src/orchestrator/adapters/impl/react_vite_stack.py`
- Test: `orchestrator/tests/contract/test_react_vite_stack.py`（追加）

- [ ] **Step 1: 追加失败测试** — `test_context_pack_includes_entry_files_and_brief`：给定 `LocateResult` + `RawRequest` + `RequestBrief`，`context_pack` 返回 `DevContext`，其中包含：入口源文件的相对路径与内容、业务 brief 文本、截图引用（base64 透传）、框选坐标、目标仓库根。`test_context_pack_expands_local_imports`（可选增强）：入口文件 import 的同目录子组件也被纳入上下文（一层即可，YAGNI）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `context_pack`** — 读 `entry_files` 内容，组装 `DevContext`（沿用 `adapters/types.py` 已定义的 `DevContext` 字段；若字段不够，在 `types.py` 补字段并在本任务说明）。一层本地 import 展开用正则提取相对 import 路径。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: ReactViteStackAdapter context_pack"`

---

## Task 4: ReactViteStackAdapter — build

**Files:**
- Modify: `orchestrator/src/orchestrator/adapters/impl/react_vite_stack.py`
- Test: `orchestrator/tests/contract/test_react_vite_stack.py`（追加）

- [ ] **Step 1: 追加失败测试** — `test_build_succeeds_on_clean_demo`（标 `@pytest.mark.slow`）：对 `demo_repo_copy` 跑 `build`，断言 `BuildResult.ok is True`。`test_build_fails_on_broken_syntax`：往一个组件文件注入语法错误后 `build`，断言 `BuildResult.ok is False` 且 `log` 非空。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `build`** — `subprocess` 在 `repo_path/frontend` 跑 `npm ci`（或检测 `node_modules` 缺失才装）+ `npm run build`；后端 `repo_path/backend` 跑 `python -m compileall` 或轻量 import 检查。`BuildResult(ok=returncode==0, log=stdout+stderr)`。前端构建用 demo 已配置的 `.npmrc`（npmmirror 镜像）。超时用 `dev_runner_timeout_seconds`。
- [ ] **Step 4: 运行确认通过**（`-m slow` 显式开启）。
- [ ] **Step 5: 提交** — `git commit -m "feat: ReactViteStackAdapter build"`

---

## Task 5: DockerPreviewAdapter — serve

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/docker_preview.py`
- Test: `orchestrator/tests/contract/test_docker_preview.py`

- [ ] **Step 1: 写失败测试**（标 `@pytest.mark.docker`，需 Docker daemon）：
  - `test_serve_returns_reachable_preview` — `serve(demo_repo_copy, "main")` 返回 `PreviewInstance`，其 `url` 在容器 healthy 后能 `GET` 到 200。
  - `test_serve_allocates_port_in_range` — 端口落在 `preview_port_min..preview_port_max`。
  - `test_serve_two_branches_get_distinct_containers` — 起两个分支，得到两个不同 `handle` + 不同端口。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `serve`** — 用 demo 自带的 `Dockerfile`。流程：`docker build -t vibe-niuma-preview-<id> <repo_path>`；分配一个空闲端口（在配置区间内扫描）；`docker run -d -p <port>:<内部端口> --network <docker_network> ...`；轮询容器 health 直到 healthy 或超时；返回 `PreviewInstance(preview_id, url=f"http://<host>:<port>", handle=<container_id>)`。失败抛异常（Pipeline 会转 `failed(building, container)`）。
- [ ] **Step 4: 运行确认通过**（`-m docker`）。
- [ ] **Step 5: 提交** — `git commit -m "feat: DockerPreviewAdapter serve"`

> **给执行者（scope fork，实现前如有疑义向人确认）：** demo 是「前端 + 后端 + MySQL」三件套。MVP 取舍：预览容器只起前端（指向共享的 demo 后端 + MySQL），还是 `docker compose` 起完整一套？推荐先只起前端容器、后端走共享实例（最快、够演示）；若 demo 改动常涉及后端接口，再升级为 compose 整套。此处按「只起前端」实现，并在 README 标注该取舍。

---

## Task 6: DockerPreviewAdapter — teardown

**Files:**
- Modify: `orchestrator/src/orchestrator/adapters/impl/docker_preview.py`
- Test: `orchestrator/tests/contract/test_docker_preview.py`（追加）

- [ ] **Step 1: 追加失败测试**（`@pytest.mark.docker`）— `test_teardown_stops_and_removes_container`：`serve` 后 `teardown`，断言容器不再存在、端口释放。`test_teardown_is_idempotent`：对已拆的实例再 `teardown` 不报错。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `teardown`** — `docker stop` + `docker rm`（best-effort、幂等：容器不存在就静默返回）；可选 `docker rmi` 清镜像。释放端口记账。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: DockerPreviewAdapter teardown"`

---

## Task 7: DevRunner 公共层

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/_dev_runner_common.py`
- Test: `orchestrator/tests/contract/test_dev_runner_common.py`

- [ ] **Step 1: 写失败测试** — 针对纯函数：
  - `test_has_uncommitted_or_new_changes` — 给定一个 temp git 仓库，无改动返回 False，有改动返回 True。
  - `test_commit_all_returns_sha` — `add -A` + commit，返回 SHA。
  - `test_collect_run_log_truncates` — 日志收集器对超长输出截断到上限并保留尾部。
  - `test_build_run_result_changed_true_false` — 构造 `RunResult`：有 diff → `changed=True`，无 diff → `changed=False, log` 说明 no-changes。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `_dev_runner_common.py`** — 提供：`has_changes(repo_path, branch) -> bool`（`git status --porcelain` + 是否有新 commit）、`commit_all(repo_path, branch, message) -> str`、`collect_log(stdout, stderr, limit) -> str`、`make_run_result(repo_path, branch, log) -> RunResult`（内部判定 changed）。两个 DevRunner 都复用。注意：Pipeline 把「改代码 + commit」整体委托给 `DevRunnerAdapter.run`（Plan 2 约束），所以 commit 发生在这里。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: DevRunner 公共层（diff 判定/commit/日志）"`

---

## Task 8: ClaudeCodeDevRunner

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/claude_code_runner.py`
- Test: `orchestrator/tests/contract/test_claude_code_runner.py`

> **LLM 非确定性：** 契约测试 mock 掉 `claude` CLI 子进程，只测确定性胶水：prompt/上下文文件落盘、命令行参数组装（`ANTHROPIC_BASE_URL` 注入、模型参数、工作目录、分支）、超时处理、子进程退出码→`RunResult` 映射、跑完调用公共层判定 changed + commit。

- [ ] **Step 1: 写失败测试** — mock `asyncio.create_subprocess_exec`：
  - `test_run_invokes_claude_with_base_url_and_model` — 断言子进程环境含 `ANTHROPIC_BASE_URL=<settings.anthropic_base_url>`，参数含 `settings.dev_model`，cwd 为 repo_path。
  - `test_run_writes_devcontext_to_prompt` — `DevContext`（brief、入口文件、截图、坐标）被组装成 claude 的输入（prompt 文件或 stdin）。
  - `test_run_nonzero_exit_raises` — CLI 非 0 退出 → `run` 抛异常（Pipeline 转 `failed(coding, runner-error)`）。
  - `test_run_zero_exit_with_diff_returns_changed` — CLI 成功且产生 diff → `RunResult.changed is True`，且公共层 `commit_all` 被调用。
  - `test_run_zero_exit_no_diff_returns_no_changes` — CLI 成功但无 diff → `RunResult.changed is False`。
  - `test_run_timeout_raises` — 超过 `dev_runner_timeout_seconds` → 抛超时异常。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `claude_code_runner.py`** — `ClaudeCodeDevRunner` 实现 `DevRunnerAdapter`。`run(repo_path, branch, ctx)`：先确保工作树在 `branch`（`git checkout branch`）；把 `ctx` 组装成 prompt（brief + 入口文件路径/内容 + 截图说明 + 框选坐标）；`asyncio.create_subprocess_exec("claude", ...)` 以非交互模式跑（claude-code 非交互参数，执行者用 claude-code-guide 或 `claude --help` 确认），环境注入 `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY=settings.llm_api_key` + 模型；带超时；跑完用 `_dev_runner_common` 判定 changed 并 commit；返回 `RunResult`。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: ClaudeCodeDevRunner"`

---

## Task 9: OpenCodeDevRunner

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/opencode_runner.py`
- Test: `orchestrator/tests/contract/test_opencode_runner.py`

> 与任务 8 同构，只是换 `opencode` CLI。opencode 原生多 provider，不需要兼容代理 —— 直接配 provider + 模型 + key。

- [ ] **Step 1: 写失败测试** — mock 子进程，对照任务 8 的用例改成 opencode 形态：`test_run_invokes_opencode_with_provider_and_model`、`test_run_writes_devcontext_to_prompt`、`test_run_nonzero_exit_raises`、`test_run_zero_exit_with_diff_returns_changed`、`test_run_zero_exit_no_diff_returns_no_changes`、`test_run_timeout_raises`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `opencode_runner.py`** — `OpenCodeDevRunner` 实现 `DevRunnerAdapter`，结构同 `ClaudeCodeDevRunner`，调 `opencode` CLI 的非交互模式（执行者确认 opencode 非交互参数），provider/model/key 从 `settings.dev_model` + `settings.llm_api_key` 取。同样复用 `_dev_runner_common`。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: OpenCodeDevRunner"`

---

## Task 10: LLM 客户端薄封装

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/_llm.py`
- Test: `orchestrator/tests/contract/test_llm.py`

- [ ] **Step 1: 写失败测试** — mock HTTP 层（`httpx`）：
  - `test_complete_text_posts_to_base_url` — `complete(prompt)` 把请求打到 `settings.anthropic_base_url`，带模型参数。
  - `test_complete_vision_includes_image` — `complete_vision(prompt, image_b64)` 用 `settings.vision_model`，请求体含图片。
  - `test_complete_raises_on_http_error` — 上游非 2xx → 抛异常。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `_llm.py`** — 一个薄 `LLMClient`：`async complete(prompt, *, model=None) -> str` 和 `async complete_vision(prompt, image_b64, *, model=None) -> str`。底层用 `httpx.AsyncClient` 打 OpenAI/Anthropic 兼容接口（经 `settings.anthropic_base_url`）。只做 BrainstormingSkill 需要的最小能力，YAGNI。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: LLM 客户端薄封装"`

---

## Task 11: BrainstormingSkill — 轻/重路径分流

**Files:**
- Create: `orchestrator/src/orchestrator/adapters/impl/brainstorming_skill.py`
- Test: `orchestrator/tests/contract/test_brainstorming_skill.py`

> **设计文档 §4.3 / §4.6：** 业务层只问业务、不碰技术；轻改动走文字问答（一次一问、最多 3 问、可跳过），重改动生成 2-3 套轻量 HTML mockup 让业务员选。HTML mockup 是一次性意图锚点，不是真实构建产物。LLM 调用全 mock。

- [ ] **Step 1: 写失败测试** — mock `LLMClient`：
  - `test_clarify_light_change_asks_text_questions` — mock LLM 把改动判为「轻」→ `clarify` 通过 `channel.ask` 问文字问题（≤3 次），产出 `RequestBrief`。
  - `test_clarify_heavy_change_presents_html_variants` — mock LLM 判为「重」→ `clarify` 调 `channel.present_variants` 给 2-3 套 `HtmlMockup`，业务员选中后 brief 带上锚点。
  - `test_clarify_respects_max_three_questions` — 即使 LLM 想问更多，也最多问 3 个。
  - `test_clarify_skippable` — 业务员对某问题回空/「跳过」，不阻塞，继续产出 brief。
  - `test_clarify_prompt_contains_no_tech_constraint` — 组装给 LLM 的 system prompt 含「只问业务、禁止技术」约束（断言 prompt 字符串包含该约束）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `brainstorming_skill.py`** — `BrainstormingSkill` 实现 `InteractionSkill`。`clarify(raw, channel)`：① 用 `LLMClient.complete_vision`（截图 + requestText）让模型判定轻/重 + 给出要问的业务问题或要生成的方案方向；② 轻路径：循环 `channel.ask`（≤3，可跳过），收集回答；③ 重路径：让 LLM 生成 2-3 套独立 HTML mockup，`channel.present_variants`，记录选中项；④ 汇总成 `RequestBrief`（业务级文本 + 可选 HTML 锚点）。prompt 层硬编码「只问业务结果，绝不涉及技术/组件/文件」约束。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `git commit -m "feat: BrainstormingSkill 轻/重路径自适应澄清"`

---

## Task 12: 接线 build_pipeline 到真实 adapter

**Files:**
- Modify: `orchestrator/src/orchestrator/main.py`
- Test: `orchestrator/tests/test_build_pipeline_wiring.py`

- [ ] **Step 1: 写失败测试** — `test_build_pipeline_uses_real_adapters`：构造 `AppState`，`build_pipeline(db)` 返回的 `Pipeline` 的 4 个 adapter 是真实类型。`test_build_pipeline_dev_runner_follows_config`：`settings.dev_runner="opencode"` 时拿到 `OpenCodeDevRunner`，`"claude-code"` 时拿到 `ClaudeCodeDevRunner`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 改 `AppState.build_pipeline`：根据 `settings.dev_runner` 选 DevRunner；其余三个 adapter 直接 new 真实实现；`LLMClient` 注入 `BrainstormingSkill`。**这是 Plan 3 唯一的 Orchestrator 主体改动点**（设计文档 §2 架构方案）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 回归** — 跑全套测试（排除 `-m "docker or slow or e2e"`）确认 Plan 2 的 79 测试 + 新契约测试全绿。注意：`test_api.py`/`test_api_sse.py`/`test_integration.py` 现在会用真实 adapter —— 它们依赖 fake 行为，需要在各自的 `client` fixture 里 monkeypatch `build_pipeline` 回 fake 装配（把 Plan 2 的 fake 装配抽成一个共用 helper，测试里复用）。这是预期调整，不是回归。
- [ ] **Step 6: 提交** — `git commit -m "feat: build_pipeline 接线真实 adapter"`

---

## Task 13: 真实 E2E 冒烟（默认 skip）

**Files:**
- Create: `orchestrator/tests/test_e2e_smoke.py`
- Modify: `orchestrator/README.md`

- [ ] **Step 1: 写冒烟测试** — `test_e2e_smoke.py`，标 `@pytest.mark.skipif(not os.getenv("VIBE_NIUMA_E2E"), reason="需真实模型+Docker")`。内容：对 demo 仓库副本，跑一个已知简单改动（如「把 /settings 的保存按钮文案改成『立即保存』」），走真实 `BrainstormingSkill`（脚本化回答澄清）+ 真实 DevRunner + 真实 build + 真实 Docker preview，断言：流水线到 `preview-ready`、git diff 非空、预览 URL 可达。
- [ ] **Step 2: 文档** — `README.md` 加「真实 E2E 冒烟」一节：如何设 `VIBE_NIUMA_E2E=1` + 模型 key + Docker 来跑。
- [ ] **Step 3: 提交** — `git commit -m "test: 真实 E2E 冒烟（默认 skip）"`

> 本任务不要求在执行时真跑通（依赖用户提供的模型 key + ECS/Docker 环境）；要求测试代码完整、可被一条命令开启。真正的端到端验证在 Plan 5。

---

## Task 14: 契约测试整理 + pytest 标记

**Files:**
- Modify: `orchestrator/pyproject.toml`
- Modify: `orchestrator/README.md`

- [ ] **Step 1** — 在 `pyproject.toml` 的 pytest 配置注册 markers：`docker`、`slow`、`e2e`。
- [ ] **Step 2** — README 补充：`venv/bin/pytest`（快测，默认不含 docker/slow/e2e）、`venv/bin/pytest -m docker`（需 Docker）、契约测试的运行说明。
- [ ] **Step 3: 全量回归** — 跑 `venv/bin/pytest -m "not e2e"`（含 docker/slow，前提是本地 Docker 可用）确认全绿。
- [ ] **Step 4: 提交** — `git commit -m "chore: 契约测试 pytest 标记与文档"`

---

## 验收标准（Plan 3 完成定义）

- [ ] 4 个 adapter 各有真实实现，均实现 Plan 2 的对应 `Protocol`，放在 `adapters/impl/`。
- [ ] `ReactViteStackAdapter.locate` 对 demo 4 条路由（含动态 `/orders/:id`）契约测试通过。
- [ ] `DockerPreviewAdapter` 能真起容器、拆容器（`-m docker` 通过）。
- [ ] 两个 DevRunner 的子进程编排逻辑有 mock 测试覆盖；`build_pipeline` 按配置选 runner。
- [ ] `BrainstormingSkill` 的轻/重路径分流、≤3 问、可跳过有 mock 测试覆盖。
- [ ] `main.py` 的 `build_pipeline` 是唯一主体改动点；Plan 2 的 79 测试经必要的 fake 注入调整后仍全绿。
- [ ] 真实 E2E 冒烟测试代码完整，靠 `VIBE_NIUMA_E2E=1` 开启。
- [ ] `git status` 干净。

---

## 需要用户提供（运行 Plan 3 前的一次性清单）

1. **dev runner 工具与模型**：确认默认用 `claude-code` 还是 `opencode`；`dev_model` 用哪个模型（DeepSeek `deepseek-chat` / 通义千问 / 其它）。
2. **Anthropic-compatible 代理**：claude-code-router 还是 LiteLLM 还是 one-api —— 选一个；代理监听地址（`anthropic_base_url`）。
3. **模型 API key**：`llm_api_key`（dev runner + 澄清共用，或分开给）。
4. **视觉模型**：澄清阶段看截图用哪个 vision model（如 `qwen-vl-plus`）+ key。
5. **本地开发环境确认**：本机是否已装 `claude` / `opencode` CLI + Docker Desktop；若契约测试要在 ECS 上跑，提供 ECS 访问方式。
6. **预览容器范围决策**（Task 5 的 scope fork）：预览只起前端容器、还是 compose 起前后端整套？
7. **demo 路由文件**：确认 `demo/frontend/src` 的实际路由写法（执行者会先读，但若你希望某种约定可提前说）。
