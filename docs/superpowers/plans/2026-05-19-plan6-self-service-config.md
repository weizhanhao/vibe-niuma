# Plan 6 — 自助配置 + 帮助引导（单仓）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把所有需要业务员/运营手动操作才能跑起来的配置（orchestrator 地址、API key、模型选择、git 仓库路径），全部挪到 Chrome 扩展的设置面板里。第一次安装扩展时启动 4 步引导向导；之后随时在设置面板编辑。每个字段右边一个 `?` 圈，hover/点击展开 markdown 指引（去哪买服务器、去哪申请 DeepSeek key、阿里 DashScope 怎么开通）。非程序员用户从「装好扩展」到「跑通第一条 CR」的中间步骤 < 5 步、< 10 分钟。

**Architecture:**
- 扩展端：`SetupWizardPanel`（首次引导，4 步）+ 扩展后的 `SettingsPanel`（日常编辑）+ `HelpBubble` 通用组件（`?` 圈 → markdown 弹窗）。配置存 `chrome.storage.local`，类型走 zod 校验。
- Orchestrator 端：新增 `/admin/config` REST GET/PUT，配置以 `system_config` 表持久化；启动时 bootstrap 自 `.env`，运行时可被 PUT 覆盖、热生效。配置带版本号（`config_version`），乐观锁。
- 鉴权：`/admin/*` 路径必须带 `X-Admin-Token` 头校验。token 在 orchestrator 首次启动时生成（写 `/opt/vibe-niuma/admin.token`），用户从 ECS 复制一次到扩展，之后由扩展存。
- 帮助内容：markdown 文件 bundle 进扩展 dist；指向外部链接（DashScope 控制台、DeepSeek 注册页等），不抄具体步骤文案（容易过期）。

**Tech Stack:** 沿用现有 —— extension 端 React + Vite + `@crxjs/vite-plugin` + vitest；orchestrator 端 FastAPI + SQLAlchemy + pytest。新加：扩展端 `react-markdown` 渲染帮助内容；扩展端 zod 校验配置 schema。

---

## 前置约定（每个任务都假定已满足）

- Plan 1-5 已合并 main，vibe-niuma v0.1.0-mvp 跑得通。
- 用户拿到 ECS 后**至少**还要 ssh 进去拷 `/opt/vibe-niuma/admin.token` 一次 —— 这个不能省（扩展没法读 ECS 文件）。其他所有「填表」操作都搬进扩展。
- 多仓不在本 plan 范围（见 Plan 7）。本 plan 假设 `DEMO_REPO_PATH` 仍是单一目录。
- 单用户 / 单 ECS / 单 admin token。安全模型与设计文档 §2 一致。
- 帮助内容用中文撰写，对外链接（控制台 URL）允许英文。
- 在新分支 `plan6-self-service-config` 上做。

## File Structure

```
extension/
  src/
    ui/
      panels/
        SetupWizardPanel.tsx        # 首次引导（4 步：URL → token → API keys → 完成）
        SettingsPanel.tsx           # 重写，支持分组 + ? 帮助 + 服务器同步状态
      components/
        HelpBubble.tsx              # (?) 圈通用组件，hover 浮层 markdown
        WizardStep.tsx              # 单步骤布局
      helpContent/                  # 一个字段一个 .md，bundle 进 dist
        orchestrator-url.md
        admin-token.md
        deepseek-key.md
        dashscope-key.md
        ecs-setup.md                # 「服务器去哪买」总指引
        git-repo-path.md
    lib/
      config.ts                     # chrome.storage 包装 + zod schema + 首次安装检测
      admin-client.ts               # /admin/config GET/PUT（带 token header）
  tests/
    setup-wizard.test.tsx
    settings-panel.test.tsx
    config-schema.test.ts
    admin-client.test.ts

orchestrator/
  src/orchestrator/
    admin.py                        # /admin/config GET/PUT 路由
    system_config.py                # ORM model + repo
    auth.py                         # X-Admin-Token 中间件
    config.py                       # 现 Settings 类调整：runtime override 来自 DB
  tests/
    test_admin_endpoint.py
    test_system_config.py
    test_admin_auth.py

deploy/
  systemd/vibe-niuma-orchestrator.service  # 启动时生成 /opt/vibe-niuma/admin.token
  deploy.sh                              # rsync 不擦 admin.token
```

## 配置 Schema（核心契约）

```typescript
// extension/src/lib/config.ts
const ConfigSchema = z.object({
  // === 本地（扩展独占） ===
  orchestratorUrl: z.string().url(),                  // http://ECS:9000
  adminToken: z.string().min(20),                     // 从 /opt/vibe-niuma/admin.token 拷
  configVersion: z.number().int().default(0),         // 服务端版本，PUT 时回带

  // === 服务端镜像（拉/推 /admin/config 同步） ===
  server: z.object({
    // 模型
    devRunner: z.enum(['opencode', 'claude-code']).default('opencode'),
    devModel: z.string().default('deepseek/deepseek-v4-flash'),
    visionModel: z.string().default('qwen-vl-plus'),
    // API key（写到服务端 .env 或 DB，重启相关服务）
    deepseekApiKey: z.string().optional(),
    dashscopeApiKey: z.string().optional(),
    anthropicApiKey: z.string().optional(),
    // 项目
    demoRepoPath: z.string().default('/opt/vibe-niuma/demo'),
    previewBackendUrl: z.string().default('http://vibe-niuma-demo-backend:8000'),
  }),
});
```

服务端 `system_config` 表只有 1 行（singleton），字段对应 `server.*`。PUT 时写 DB + 决定是否重启关键服务（API key 改 → 重启 LiteLLM；dev runner / 模型名改 → 仅热重载 `get_settings()`，不重启 orchestrator）。

---

## Task 1: orchestrator 端 — system_config 表 + ORM + repo

**Files:**
- Create: `orchestrator/src/orchestrator/system_config.py`
- Create: `orchestrator/tests/test_system_config.py`
- Modify: `orchestrator/src/orchestrator/models.py`（追加表）

- [ ] **Step 1: 写失败测试** — `test_singleton_creates_with_defaults` / `test_update_increments_version` / `test_concurrent_update_latest_wins`
- [ ] **Step 2: 跑测试确认 RED**
- [ ] **Step 3: 实现** — `SystemConfig` ORM（id 固定 1，字段同 schema 的 `server.*`，加 `version: int`，`updated_at: datetime`）；`SystemConfigRepository.get_or_create() / update(patch, expected_version)`
- [ ] **Step 4: 跑测试确认 GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): system_config 表 + repo`

## Task 2: orchestrator 端 — admin token + 中间件

**Files:**
- Create: `orchestrator/src/orchestrator/auth.py`
- Create: `orchestrator/tests/test_admin_auth.py`
- Modify: `deploy/systemd/vibe-niuma-orchestrator.service` 启动时 `ExecStartPre` 生成 admin.token

- [ ] **Step 1: 写失败测试** — `test_admin_endpoint_requires_token` / `test_admin_token_wrong_returns_401` / `test_admin_token_correct_passes`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `verify_admin_token` FastAPI dependency：从 `X-Admin-Token` header 取，对比 `/opt/vibe-niuma/admin.token` 内容（首次启动时如不存在则 `secrets.token_urlsafe(32)` 写入并 chmod 600）
  - systemd unit `ExecStartPre=/bin/bash -c 'test -f /opt/vibe-niuma/admin.token || (head -c 24 /dev/urandom | base64 > /opt/vibe-niuma/admin.token && chmod 600 /opt/vibe-niuma/admin.token)'`
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): /admin/* token 鉴权`

## Task 3: orchestrator 端 — /admin/config GET/PUT

**Files:**
- Create: `orchestrator/src/orchestrator/admin.py`
- Create: `orchestrator/tests/test_admin_endpoint.py`
- Modify: `orchestrator/src/orchestrator/main.py`（include_router）

- [ ] **Step 1: 写失败测试** —
  - `test_get_returns_current_config_with_version`
  - `test_put_with_expected_version_succeeds_and_bumps`
  - `test_put_with_stale_version_returns_409`
  - `test_put_with_invalid_field_returns_422`
  - `test_put_triggers_litellm_restart_when_provider_key_changed`（mock subprocess）
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `GET /admin/config` → `{config: {...}, version: int}`
  - `PUT /admin/config` body `{config: {...}, expectedVersion: int}` → 409 if mismatch / 200 + new version
  - 副作用：监测哪些字段变了，决定是否触发 systemd restart：API key 变 → `subprocess.run(["systemctl", "restart", "vibe-niuma-llm-proxy"])`；模型名变 → 仅 invalidate settings 缓存（`get_settings.cache_clear()`）
  - settings 热重载：把 `Settings()` 调用包装成 `@lru_cache` 的 `get_settings()`，返回最新值
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): /admin/config GET/PUT + 热重载 + 乐观锁`

## Task 4: 扩展端 — config.ts（chrome.storage + zod）

**Files:**
- Create: `extension/src/lib/config.ts`
- Create: `extension/tests/config-schema.test.ts`

- [ ] **Step 1: 写失败测试** —
  - `test_loads_empty_on_first_install`（无存储 → `null`）
  - `test_loads_valid_config`
  - `test_rejects_invalid_url`
  - `test_saves_partial_update_preserves_other_fields`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - 类型 + zod schema（上面那份）
  - `loadConfig(): Promise<Config | null>` / `saveConfig(patch: Partial<Config>): Promise<Config>` / `isConfigured(): Promise<boolean>`（最低字段：URL + token）
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): config.ts + zod schema`

## Task 5: 扩展端 — admin-client.ts

**Files:**
- Create: `extension/src/lib/admin-client.ts`
- Create: `extension/tests/admin-client.test.ts`

- [ ] **Step 1: 写失败测试** —
  - `test_get_config_includes_token_header`
  - `test_put_passes_expected_version`
  - `test_put_409_throws_stale_version_error`
  - `test_network_error_throws_typed_error`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** — `AdminClient` 类，`getConfig() / putConfig(serverPatch, expectedVersion)`，错误用 `AdminClientError` 子类（`StaleVersionError` / `AuthError` / `NetworkError`）
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): admin-client REST 封装`

## Task 6: 扩展端 — HelpBubble 组件

**Files:**
- Create: `extension/src/ui/components/HelpBubble.tsx`
- Create: `extension/src/ui/components/HelpBubble.css`
- Create: `extension/tests/help-bubble.test.tsx`

- [ ] **Step 1: 写失败测试** —
  - `test_renders_question_mark_icon`
  - `test_clicking_shows_popover_with_markdown`
  - `test_clicking_outside_closes_popover`
  - `test_external_links_open_new_tab`（`target="_blank" rel="noopener"`）
  - `test_keyboard_a11y_escape_closes`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - props: `{content: string /* markdown */, ariaLabel: string}`
  - 圆形 `?` 图标（14px、`var(--accent)` 描边、hover 填充），点击展开 floating popover（portal 挂 body，避免被父级 overflow 切）
  - 用 `react-markdown` 渲染，外链一律 `target="_blank" rel="noopener noreferrer"`
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): HelpBubble 通用组件`

## Task 7: 扩展端 — 帮助内容编写

**Files:**
- Create: `extension/src/ui/helpContent/orchestrator-url.md`
- Create: `extension/src/ui/helpContent/admin-token.md`
- Create: `extension/src/ui/helpContent/deepseek-key.md`
- Create: `extension/src/ui/helpContent/dashscope-key.md`
- Create: `extension/src/ui/helpContent/ecs-setup.md`
- Create: `extension/src/ui/helpContent/git-repo-path.md`
- Modify: `extension/vite.config.ts`（让 `?raw` 导入 markdown）

每篇 50-150 字，结构：「这是什么」+「在哪获取」（带外链）+「如何验证已经对了」。**不抄文案、只指路**。

例 `deepseek-key.md`：
```markdown
**DeepSeek API key** 用于 dev runner（改代码的 AI）和澄清模型。

1. 到 [platform.deepseek.com](https://platform.deepseek.com) 注册
2. 「API Keys」→「创建新密钥」
3. 复制 `sk-...` 开头的字符串粘到这里

**验证**：填好保存后，新建一条 CR 看 sidebar 是否有「视觉模型回答中」流式输出。
```

例 `ecs-setup.md`：
```markdown
**ECS 是部署 orchestrator 的服务器**。vibe-niuma 需要一台能跑 Docker 的 Linux 机器（≥ 4 GiB 内存）。

**新手推荐**：
- [阿里云轻量应用服务器](https://www.aliyun.com/product/swas)（最便宜，~60 元/月）
- [腾讯云轻量](https://cloud.tencent.com/product/lighthouse)

**关键设置**：
- 镜像选 **Alibaba Cloud Linux 4** 或 **Ubuntu 22.04**
- 安全组开放：22（SSH）/ 8000 / 8787 / 9000 / 5100-5199
- 创建后用 SSH 登录跑 `bash deploy/provision.sh`（首次开荒脚本）

**详细步骤** 见仓库 `docs/RUNBOOK.md`。
```

- [ ] **Step 1: 起所有 6 篇 markdown 草稿**
- [ ] **Step 2: 内部 review**（自审：每篇都有外链 + 都告诉了「如何验证」）
- [ ] **Step 3: 配 vite 让 markdown `?raw` import**
- [ ] **Step 4: 提交** — `docs(extension): 6 篇字段帮助文案`

## Task 8: 扩展端 — SetupWizardPanel（4 步首次引导）

**Files:**
- Create: `extension/src/ui/panels/SetupWizardPanel.tsx`
- Create: `extension/src/ui/components/WizardStep.tsx`
- Create: `extension/tests/setup-wizard.test.tsx`

**4 步设计**：
1. **服务器地址** — orchestrator URL + 「测试连接」按钮（GET /health）
2. **管理员令牌** — admin token + 「验证」按钮（GET /admin/config）
3. **模型 API key** — DeepSeek + DashScope（最低）；填好 PUT /admin/config 写到服务端
4. **完成** — 总结 + 「开始使用」按钮（关掉 wizard、跳到主 CapturePanel）

每步都能「上一步」「下一步」。每个字段右边都挂 `HelpBubble`。每步底部「跳过引导」逃生口（会进 SettingsPanel + 提示未完成字段）。

- [ ] **Step 1: 写失败测试** —
  - `test_step1_url_test_button_calls_health_endpoint`
  - `test_step1_invalid_url_disables_next`
  - `test_step2_validates_token_via_get_admin_config`
  - `test_step3_save_pushes_to_admin_config_with_correct_version`
  - `test_step4_complete_sets_wizard_done_flag`
  - `test_full_flow_4_steps_renders_main_panel_at_end`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现**
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): SetupWizardPanel 4 步首次引导`

## Task 9: 扩展端 — SettingsPanel 重写

**Files:**
- Modify: `extension/src/ui/panels/SettingsPanel.tsx`
- Modify: `extension/tests/settings-panel.test.tsx`

**布局**：
- 折叠分组：「服务器连接」「AI 模型」「API key」「项目路径」
- 每组顶部「上次同步：YYYY-MM-DD HH:MM · 版本 v3」
- 每个字段 `HelpBubble`
- 底部「保存并同步」按钮 → PUT /admin/config，状态显示「同步中… / 已同步 / 失败 + 错误原因」
- 「重置为服务器版本」按钮（拉 GET 覆盖本地）

- [ ] **Step 1: 写失败测试** —
  - `test_loads_existing_config_into_form`
  - `test_save_with_stale_version_shows_409_dialog_offering_reload`
  - `test_save_with_invalid_field_shows_inline_error`
  - `test_changing_api_key_triggers_litellm_restart_hint`（UI 提示 "保存后会重启 LiteLLM 约 5s"）
  - `test_help_bubble_visible_for_every_field`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现**
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): SettingsPanel 重写，分组 + 帮助气泡 + 服务端同步`

## Task 10: 扩展端 — 路由：未配置 → wizard

**Files:**
- Modify: `extension/src/ui/App.tsx`
- Modify: `extension/src/background/service-worker.ts`（移除硬编码 URL）
- Create: `extension/tests/onboarding-routing.test.tsx`

- [ ] **Step 1: 写失败测试** —
  - `test_first_install_renders_setup_wizard_not_capture`
  - `test_configured_renders_capture_panel`
  - `test_settings_panel_accessible_via_gear_icon_after_wizard`
  - `test_service_worker_reads_url_from_config_not_hardcoded`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `App.tsx` 先 `loadConfig()`，无值 → `<SetupWizardPanel/>`，有值 → 现有主路由
  - service-worker `orchestratorClient` 构造时从 `chrome.storage` 取 URL + token；变化时（`chrome.storage.onChanged`）重建 client
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): 首次安装路由到引导向导`

## Task 11: deploy.sh / RUNBOOK 配套修改

**Files:**
- Modify: `deploy/deploy.sh`（不擦 admin.token；first-run 输出 token 路径）
- Modify: `deploy/healthcheck.sh`（追加 admin API 鉴权通的检测）
- Modify: `docs/RUNBOOK.md`（新章节「Plan 6 自助配置后的运维」）
- Modify: `README.md`（快速开始改成「装扩展 → 走 wizard」）

- [ ] **Step 1: 改 deploy.sh** —
  - rsync `--exclude 'admin.token'`
  - 部署完成提示「Admin Token: ssh ECS 'cat /opt/vibe-niuma/admin.token'」
- [ ] **Step 2: 改 healthcheck.sh** —
  - 第 11 项：admin API 鉴权通
- [ ] **Step 3: 改 README**
- [ ] **Step 4: 跑一遍 deploy + healthcheck 确认绿**
- [ ] **Step 5: 提交** — `chore: deploy/healthcheck/README 配合 Plan 6`

## Task 12: 端到端联调

**Files:**
- Create: `extension/tests/integration-plan6.test.tsx`

- [ ] **Step 1: 写集成测试** —
  - 模拟全新安装（chrome.storage 空）
  - mock fetch：`/health` 200、`/admin/config` GET 200 + version 0、PUT 200 + version 1
  - 走完 wizard 4 步，断言：
    - 每步表单可填、`?` 帮助可展开
    - 完成后 chrome.storage 写入 + PUT 调用参数正确
    - 主 CapturePanel 出现
- [ ] **Step 2: GREEN**
- [ ] **Step 3: 手动验证** — 部署 + 在真 ECS 上走一遍（可选）
- [ ] **Step 4: 提交** — `test: Plan 6 端到端集成测试`

---

## 验收标准（Plan 6 完成定义）

- [ ] 全新装扩展（清 chrome.storage）打开 → 出现 SetupWizardPanel 4 步引导
- [ ] 每个表单字段都有 `?` 帮助气泡，点击展开 markdown，外链全部新标签打开
- [ ] 走完 wizard：本地配置 + 服务端 `/admin/config` 都写好；CapturePanel 出现可正常用
- [ ] SettingsPanel 可见、可改、可同步；改 API key 显示「会重启 LiteLLM」hint
- [ ] 乐观锁：版本不匹配返回 409 + UI 弹「拉服务端最新版本？」
- [ ] orchestrator `/admin/config` 路径需要 token，无 token 401，错 token 401
- [ ] orchestrator 重启后 admin.token 文件保留（不被 deploy 擦掉）
- [ ] vitest + pytest 全绿；`git status` 干净
- [ ] README 重写「快速开始」，**新用户不再需要编辑 deploy/.env**（除了 ECS_HOST / ECS_SSH_KEY 这种纯部署级字段）

---

## 需要用户提供（运行 Plan 6 前的一次性清单）

1. 决定：admin token 的存储方式 —— `/opt/vibe-niuma/admin.token` 文本文件（plan 默认）还是环境变量？选默认即可。
2. （可选）帮助内容外链清单复查 —— 中国地区可访问的 DeepSeek 控制台、阿里 DashScope 控制台 URL。
3. UI 风格：`?` 帮助气泡用 hover-on 还是 click-on？plan 默认 click-on（移动端友好）。可改。
4. 是否在 Wizard 第 0 步加「一键检测本机/服务器」按钮（检测 Docker / SSH）？plan 默认不加（扩展是浏览器进程，无法 ssh）。
