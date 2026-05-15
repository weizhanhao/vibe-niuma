# doskill

AI 原生低代码平台 MVP。业务员在 web 产品页面上**框选一块区域** + 自然语言说需求 → 浏览器扩展捕获 → Orchestrator 跑 AI dev runner 在隔离分支上改代码 → Docker 预览 → 业务员看效果 → 确认合并回 main → main demo 站自动重建。

```
[业务员 / Chrome 扩展]
  框选 + 「加个搜索框」
        │
        ▼
[Orchestrator FastAPI]                   ┌─ LiteLLM ─→ DeepSeek v4-flash
  clarify  (Qwen-VL streaming) ─────────┤
  locate   (route 表)                    └─ Qwen-VL-Plus
  coding   (opencode CLI 改代码 + commit)
  building (vite build + docker run)
  preview-ready ──→ http://ECS:5xxx     业务员看预览
        │
        ▼ 「确认合并」
  git rebase + ff-merge → main          main demo @ :5199 自动重建
```

设计文档：[`docs/superpowers/specs/2026-05-14-ai-native-low-code-design.md`](docs/superpowers/specs/2026-05-14-ai-native-low-code-design.md)

## 目录

- [架构](#架构)
- [快速开始](#快速开始)
- [E2E 跑一条 CR](#e2e-跑一条-cr)
- [日常运维](#日常运维)
- [踩过的坑 ⚠️（务必看一遍）](#踩过的坑-务必看一遍)
- [项目结构](#项目结构)
- [开发](#开发)

---

## 架构

**4 个 Adapter**（`orchestrator/src/orchestrator/adapters/interfaces.py`）：

| Adapter | 干什么 | 当前实现 |
|---|---|---|
| `InteractionSkill` | 看截图 + 项目知识，判断业务意图、出澄清问题 | `BrainstormingSkill` + Qwen-VL streaming |
| `StackAdapter` | URL → 源文件，打包代码上下文，跑构建 | `ReactViteStackAdapter`（vite build） |
| `DevRunnerAdapter` | 业务 brief → 真改代码 + commit | `OpenCodeDevRunner`（opencode CLI + DeepSeek） |
| `PreviewAdapter` | 分支 → 隔离预览容器 | `DockerPreviewAdapter`（vite dev :5173） |

**FSM**：`created → clarifying → located → coding → building → preview-ready → merged / discarded / failed`。`preview-ready` 是非终态（仍占配额），由 merge / discard / TTL 释放。

**SSE 事件类型**：`status` / `question` / `variants` / `log` / `question-resolved`。扩展端 mirror reducer 在 `extension/src/background/request-store.ts`。

---

## 快速开始

### 1. 准备依赖

**本地**（部署机）：
- Python 3.11+，Node 22+，Docker
- `opencode` CLI（`brew install sst/tap/opencode` 或 [opencode.ai](https://opencode.ai)）
- SSH 私钥能登 ECS

**ECS**（推荐阿里云 Alibaba Cloud Linux 4 / 8 GiB / Docker 已装）：
- 安全组开放：22 / 8000 / 8787 / 9000 / 5100-5199（预览端口段）
- 装好 git、bash、python3-venv

### 2. API key

- **DeepSeek**：[platform.deepseek.com](https://platform.deepseek.com)（dev runner + 澄清模型，`deepseek-v4-flash` 够用）
- **DashScope / 通义**：[bailian.console.aliyun.com](https://bailian.console.aliyun.com)（视觉模型 `qwen-vl-plus`）

### 3. 配置

```bash
cp deploy/env.example deploy/.env
# 编辑 deploy/.env：
#   ECS_HOST, ECS_SSH_KEY, PREVIEW_HOST 改成你的 ECS 公网 IP / 私钥路径
#   DEEPSEEK_API_KEY, DASHSCOPE_API_KEY 填真 key
#   ⚠️ 注释只能独占一行，不能写在 KEY=value 同一行（看「踩过的坑」#1）
```

### 4. 部署

```bash
bash deploy/deploy.sh        # rsync 代码 + 装依赖 + 起容器 + systemd 重启
bash deploy/healthcheck.sh   # 期望「通过 10 · 失败 0」
```

第一次跑会触发 `/init`（让 opencode 扫 demo 仓库写 `AGENTS.md`，约 60-120s）：

```bash
curl http://ECS:9000/repo/status
# 期望最终 {"status":"ready","doc_exists":true,...}
```

### 5. 加载 Chrome 扩展

```bash
cd extension && npm install && npm run build
```

然后 `chrome://extensions/` → 打开「开发者模式」 → 「加载已解压的扩展程序」 → 选 `extension/dist/`。

扩展图标点开右键 → 「打开侧边栏」。

---

## E2E 跑一条 CR

1. 浏览器打开 `http://ECS:5199/orders`（main demo 样板间）
2. 侧边栏输需求「加个搜索框」→ 点 📍 框选（或直接「→ 直接提交」）
3. 框选要改的区域 → Review 页确认蓝框位置 → 「确认提交」
4. 看流式日志：澄清 → 路由解析 → opencode 改代码（每行实时滚动）→ npm build → docker run → 预览就绪
5. 第 5 步点「新标签打开」看预览，OK 就「确认合并 →」
6. 合并后会自动重建 main demo 站，刷新 `:5199` 就看到新 UI

中间任何阶段卡住，看 SSE log 里的「⏳ ... 已 Xs」心跳判断是 LLM 慢 / runner 跑得久 / 真死了。

---

## 日常运维

### 看服务状态

```bash
# 一键全检
bash deploy/healthcheck.sh

# 单看 orchestrator 实时日志
ssh root@ECS "journalctl -u doskill-orchestrator -f"

# 单看 LiteLLM 代理日志
ssh root@ECS "journalctl -u doskill-llm-proxy -f"
```

### 重启服务

```bash
ssh root@ECS "systemctl restart doskill-orchestrator"
ssh root@ECS "systemctl restart doskill-llm-proxy"
```

### 改 .env 后

```bash
bash deploy/deploy.sh       # rsync 新 .env + systemctl restart
```

### 清孤儿预览容器

正常情况 merge/discard 会自动拆容器。手动兜底：

```bash
ssh root@ECS "docker ps --filter 'name=doskill-preview' -q | xargs -r docker rm -f"
```

### 强制重做 /init（AGENTS.md 想让 AI 重写）

```bash
curl -X POST http://ECS:9000/repo/init
curl http://ECS:9000/repo/status   # 等 ready
```

### 重建 main demo（手工）

正常合并后 orchestrator 会异步触发，手工兜底：

```bash
ssh root@ECS "cd /opt/doskill && bash deploy/main-demo.sh --rebuild"
```

---

## 踩过的坑 ⚠️（务必看一遍）

> 这一节是 MVP 跑通过程中**每一个**导致小时级停摆的坑。每一条都有过 PR / commit，写下来是为了「下次不要再上同一个钩」。

### 部署 / 配置类

#### 1. `.env` 行内 `#` 注释被 systemd 当成 value 字面量

**症状**：opencode rc=1，LiteLLM 报「Invalid model name passed in model=deepseek/deepseek-v4-flash   # opencode 要 ...」。
**根因**：`systemd` 的 `EnvironmentFile=` **不剥** 行内 `#` 注释（跟 bash `set -a && . .env` 不一样）。
**修**：注释必须独占一行。

```bash
# WRONG
DEV_MODEL=deepseek/deepseek-v4-flash   # opencode 要 provider/model 格式

# CORRECT
# opencode 要 provider/model 格式
DEV_MODEL=deepseek/deepseek-v4-flash
```

#### 2. systemd 不传 `HOME`，opencode rc=1 空 stderr

**症状**：`/init CLI 非 0 退出 (rc=1)`，stderr 完全空白。手动 ssh 登录跑 `opencode run "..."` 又完全正常。
**根因**：systemd 默认不设 `HOME`；opencode 读 `~/.config/opencode/`，找不到就直接退出且不打错。
**修**：systemd unit 显式 `Environment=HOME=/root`（见 `deploy/systemd/doskill-orchestrator.service`）。

#### 3. 嵌套 heredoc 同名 `EOF` 提前关掉外层

**症状**：`deploy.sh` 输出 `warning: here-document delimited by end-of-file` + `chmod: .env: No such file or directory`；后面的 systemd restart 步骤悄无声息被跳过 → 改了代码部署没生效。
**根因**：

```bash
ssh ... bash <<EOF        # 外层
  cat > .env <<EOF        # 内层 — 同名 EOF 在这里就关掉了外层！
  KEY=value
  EOF
  ... 后面全没执行 ...
EOF
```

**修**：内层用不同 delimiter，例如 `<<INNER_ENV`。

#### 4. `rsync --delete` 没排 `.git`，每次 deploy 擦光 demo git 历史

**症状**：用户在扩展点「确认合并」后 sidebar 显示「合并成功」，回 `:5199` 刷新页面还是老 UI；ECS `cd /opt/doskill/demo && git log` 只有「demo init」一条 commit，所有 cr/ 分支消失。
**根因**：本机 `demo/` 没有自己的 `.git/`（它是 doskill 主仓的子目录），ECS 的 `.git/` 是 orchestrator pipeline 自己 init 的。`rsync --delete` 会把目的端有、源端没有的文件全删掉 → ECS 的 `.git/` 被擦 → 接着 deploy 脚本「无 .git 就 git init」重建空 repo → 历史全丢。
**修**：`rsync` 加 `--exclude '.git' --exclude 'AGENTS.md'`，并把 `--delete` 改成 `--update`，避免本机老源码盖掉 dev runner 改过的工作树。

#### 5. LiteLLM `load_dotenv()` 向上爬，捞到 orchestrator 的 `DATABASE_URL`

**症状**：`doskill-llm-proxy` 启动时 prisma crash，报数据库连接错。
**根因**：LiteLLM 启动会 `load_dotenv()` 从 CWD 一路向上找 `.env`，会找到 `/opt/doskill/.env` 里给 orchestrator 用的 `DATABASE_URL`，触发 prisma 初始化。
**修**：在 LiteLLM 工作目录单独放一份只含 provider key 的 `.env`（deploy.sh 自动生成），并在 systemd unit 用 `ExecStart=/usr/bin/env -u DATABASE_URL ...`。

---

### Pipeline / Orchestrator 类

#### 6. `Pipeline.run()` 只 catch `_PhaseError`，git / DB 异常被吞掉

**症状**：CR 卡在 `located` 状态不动，扩展端无任何 SSE 状态更新，quota 永远不释放。
**根因**：外层 `except _PhaseError` 之外的异常（`git_manager.create_branch` 失败、DB 异常、CancelledError、编程 bug）默默逃逸，CR 永远不会到失败终态。
**修**：加 `except BaseException` 兜底 mark_failed + release quota + 发 FAILED status；`CancelledError` 重抛保留 asyncio 取消语义。

#### 7. `_spawn` 沉默吞后台 task exception

**症状**：跟 #6 关联 —— 上层异常根本没机会写到 journalctl，凭空消失。
**修**：`_spawn` 加 `task.add_done_callback`，遇到非 cancelled 异常 `logger.exception` 出去。

#### 8. dev runner 启动期 30+s 完全无输出 → 看着像死了

**症状**：扩展端 log 只有「▸ 起 dev runner...」，30s 后还是没动静。
**根因**：opencode 启动初期不打 stdout（在初始化 session / 加载 prompt），用户以为卡死了。
**修**：`stream_subprocess(heartbeat_seconds=5)` —— 子进程超过 5s 静默就发一条 `⏳ runner 静默 Xs（累计 Ys）...`。

#### 9. LLM HTTP 调用全程黑盒

**症状**：clarifying 阶段「▸ 问视觉模型判断业务意图」之后 10-30s 完全静默；体验非常差。
**根因**：`complete_vision` 用 buffered POST，等服务端完整返回才有数据。
**修**：
- `LLMClient.complete_vision_stream(on_token=)` 走 OpenAI SSE，每个 delta.content chunk 回调一次。
- `BrainstormingSkill._plan` 检测到 `channel.log` 就走流式，每 32 字符或换行 flush 一行。
- 兜底心跳：`_with_heartbeat()` wrap clarify / locate / context_pack 这些没 stdout 流的 awaitable。

#### 10. merge / discard 端点不广播 SSE，扩展 mirror 卡 preview-ready

**症状**：点「确认合并」后端 200 返回成功，sidebar 还是 preview-ready 状态；再点合并就 409「已是终态」+ UI 静默无反应。
**根因**：端点只 `repo.transition(MERGED)` 写 DB，没 publish status event。扩展 mirror 永远不知道 CR 状态变了。
**修**：merge/discard/conflict 都补 `event_bus.publish(Event(type="status", data={"state": "merged/...", ...}))`。

#### 11. `preview_url` / `branch` 是 DB 字段，不在事件里 → mirror 永远 null

**症状**：第 5 步「预览地址」字段空白，「新标签打开」无效，但 `curl ECS:9000/change-requests/<id>` 显示 `preview_url` 是对的。
**根因**：`pipeline._set_state(PREVIEW_READY)` 默认只发 `{state}`；`set_preview` 写完 DB 后这两个字段没 piggyback 到 SSE。扩展 `applyEvent('status')` 也不读这俩。
**修**：preview-ready / coding 转换直接发自带 `preview_url + branch` 的 status 事件；`applyEvent('status')` 用 `??` 合并这两个字段。

#### 12. `merge_to_main` 被 build 阶段的脏工作树挡住，误报 conflict

**症状**：fail_phase=`merging` / reason=`conflict`，看着像代码冲突。
**根因**：commit_all 之后 `npm run build` 又产生新的 `dist/` 文件，工作树脏。`git rebase main` 拒绝：「cannot rebase: You have unstaged changes」。这不是真的冲突。
**修**：`merge_to_main` 进入时先 `git stash push -u`（含 untracked），merge 完 `git stash drop`。build artifact 是临时产物，丢掉无所谓。

#### 13. 终态后预览容器泄漏，端口段被吃满

**症状**：跑了几条 CR 后 5100-5199 端口段被 stale 容器占满，新预览分不到端口。
**根因**：reaper 只清 `preview-ready` 状态的容器；`merged / discarded / failed-with-preview` 的容器永远在那。
**修**：merge / discard 成功后 `_spawn(_teardown_preview_after_terminal)`，异步拆容器 + 释放端口。`app_state.preview` 改成 lifespan 单例，reaper / pipeline / teardown 共用同一份 `_used_ports`。

#### 14. `RepoInitializer.wait_ready` 在 FAILED 也死等 timeout

**症状**：`/init` 失败后，下一条 CR 在 clarifying 阶段干等 120s 才进入降级模式。
**修**：`wait_ready` 头一行检查 `self._status == FAILED` 就立刻返回 `False`，不进 `wait_for`。同时把 `_INIT_WAIT_SECONDS` 从 120 → 30。

---

### 浏览器扩展类

#### 15. MV3 content script 只在「扩展加载之后才打开/刷新的页」自动注入

**症状**：用户重载扩展后，到已经开着的 demo 页点「📍 框选」毫无反应。
**根因**：MV3 的「静态 content_scripts」只对加载之后才打开/刷新的 tab 生效。已经开着的 tab 里没有 content script，SW 的 `chrome.tabs.sendMessage` 静默 reject。
**修**：SW 在 `UI_START_CAPTURE` 时先 sendMessage 探测；catch 到「receiver not found」就 `chrome.scripting.executeScript({ files: <从 runtime manifest 取> })` 按需注入，然后 retry。
**注意**：注入路径必须从 `chrome.runtime.getManifest().content_scripts[0].js[0]` 取，**不能** hardcode `src/content/content-entry.js` —— crxjs 会把它哈希成 `assets/content-entry.ts-loader-XXX.js`。

#### 16. `captureVisibleTab` 返回的 PNG 太大（5-10MB）→ POST 卡 35s+

**症状**：点「确认提交」后请求一直 pending，30s+ 才回 200。
**根因**：Retina 屏幕的 `chrome.tabs.captureVisibleTab` 返回未压缩 PNG，base64 编码后 ~13MB。中国家用网络上传慢死。
**修**：SW 里用 `OffscreenCanvas` 把图压成 JPEG@75% / max-width 1280，典型 <300KB。

#### 17. SW 30s 闲置死亡，长跑 CR SSE 断流

**症状**：CR 跑了几分钟后 SSE 自然断开，扩展端不再收事件。
**根因**：MV3 service worker 默认 30s 没活动就被回收。
**修**：`chrome.alarms` 每 30s 触发一次 keepalive；alarm 触发时若有 in-flight CR 就重新 attach SSE 订阅。

---

### 数据 / 模型 / 网络类

#### 18. MySQL `TEXT` 64KB 装不下截图 base64

**症状**：POST `/change-requests` 写库时报 `Data too long for column 'screenshot_b64'`。
**修**：`models.py` 用 `Text().with_variant(LONGTEXT, "mysql")`。`fail_log` 同款修。

#### 19. 预览容器只起前端 → 页面没数据

**症状**：预览容器 UI 改对了，但「订单」列表是空的。
**根因**：`DockerPreviewAdapter` 只 `docker build frontend/`，没起 backend；vite proxy 把 `/api` 转给 `localhost:8000`，预览容器里啥都没有。
**修**：预览容器 `--network doskill-net` + `-e VITE_API_URL=http://doskill-demo-backend:8000`，vite dev server 把 `/api` 反代到 main demo 后端，复用 `demo` schema 真数据。

#### 20. 合并后 main demo 容器还跑老镜像

**症状**：sidebar 显示「合并成功 刷新原页面就能看到效果」，回 `:5199` 刷新还是老 UI。
**根因**：merge 端点只 `git merge`，没人重建 `doskill-demo-frontend` 容器。容器还在跑部署时 build 的旧镜像。
**修**：merge 成功后 `_spawn(_refresh_main_demo)`，异步跑 `deploy/main-demo.sh --rebuild`，stdout 行级回灌到 SSE log。脚本路径走 `MAIN_DEMO_REFRESH_SCRIPT` env（空串则跳过 = 本地开发）。

#### 21. claude-code CLI 跟 DeepSeek thinking mode 协议不兼容

**症状**：claude-code 调 `deepseek-*` 系列 400 拒收（缺 `reasoning_content`）。
**修**：默认 `DEV_RUNNER=opencode`。claude-code 留着兼容路径，但需要 Anthropic 真 key + Claude 系列模型才稳。

---

## 项目结构

```
demo/             被改的目标产品（订单管理 mini app）
  backend/        FastAPI + SQLAlchemy + MySQL
  frontend/       React 19 + Vite 6 + react-router 7
  AGENTS.md       /init 自动生成的项目知识文档（业务员看不见，给 LLM 用）

orchestrator/     FastAPI 单体
  src/orchestrator/
    main.py             REST + SSE + lifespan + _spawn
    pipeline.py         FSM 驱动器 + _phase_start/_phase_done + _with_heartbeat
    git_manager.py      create_branch / merge_to_main（带 stash 兜底）
    repo_init.py        opencode 扫仓库写 AGENTS.md
    interaction_channel.py  SSE 桥接 + question-resolved 广播
    repository.py       DB 层（SQLAlchemy）
    events.py           EventBus（按 request_id 分发 + 重连回放）
    quota.py            槽位管理
    reaper.py           闲置 CR 回收
    history_writer.py   spec/plan/result 沉淀到 .doskill/history
    adapters/
      interfaces.py     4 个 Protocol
      impl/
        brainstorming_skill.py    InteractionSkill 真实实现
        react_vite_stack.py       StackAdapter
        opencode_runner.py        DevRunner（默认）
        claude_code_runner.py     DevRunner（备选）
        docker_preview.py         PreviewAdapter
        _llm.py                   complete / complete_vision / complete_vision_stream
        _dev_runner_common.py     stream_subprocess + commit_all
  tests/                  unit + 契约 + 集成（148 项）

extension/        Chrome MV3 扩展
  src/
    background/
      service-worker.ts   多 CR 镜像 + SSE 订阅 + alarms keepalive + 按需注入
      request-store.ts    纯 reducer + chrome.storage 持久化
    content/
      content-entry.ts    监听 START_CAPTURE
      capture-overlay.ts  框选 overlay
    ui/
      index.tsx / App.tsx
      panels.tsx          所有 7 个 panel（Capture / Review / Question / Variants / Status / Preview / Failed）
      components/ConversationList.tsx
    lib/
      messages.ts         所有 SW ↔ UI 消息常量
      types.ts            SSEEvent / RequestStateMirror / PendingCapture
      orchestrator-client.ts  REST + SSE 客户端
  tests/                  78 项 vitest

deploy/           ECS 部署
  deploy.sh           主部署脚本（rsync + 装依赖 + systemd restart）
  healthcheck.sh      10 项验证
  main-demo.sh        起 / 重建 main demo 容器（业务员看的「样板间」）
  provision.sh        新 ECS 首次开荒（装 Docker / Node / opencode 等）
  systemd/
    doskill-orchestrator.service
    doskill-llm-proxy.service
  llm-proxy/
    config.yml          LiteLLM 路由（deepseek-v4-flash / qwen-vl-plus）

docs/
  superpowers/specs/   设计文档
  superpowers/plans/   5 个阶段的实现计划
  mockups/             静态 HTML demo（无后端体验）
  RUNBOOK.md           运维记录
```

---

## 开发

### 本地跑测试

```bash
# orchestrator
cd orchestrator
python3 -m venv venv && venv/bin/pip install -e ".[dev]"
venv/bin/pytest                          # 148 项

# extension
cd extension
npm install && npm test                  # 78 项 vitest
npm run build                            # 产物在 dist/

# demo
cd demo/backend && python3 -m venv venv && venv/bin/pip install -e .
cd demo/frontend && npm install
```

### 本地手玩（无 ECS）

```bash
open docs/mockups/doskill-extension-demo.html
```

### 改 adapter

换 dev runner / 换栈 / 换预览方式只动 `orchestrator/src/orchestrator/adapters/impl/` 里对应文件。`interfaces.py` 是契约，Pipeline 主体 + 测试都不用改。

### 设计原则

- **状态机驱动**：每条 CR 在 FSM 上跑一遍，状态写 DB + 广播 SSE。
- **分支 + 容器即隔离单位**：每条 CR 占一个 `cr/<id>` 分支 + 一个独立预览容器。
- **Adapter 是唯一的「栈相关」代码**：换栈不动 Orchestrator 主体。
- **OUT of scope**（设计 §8）：多角色 / 多租户 / 多栈 / 鉴权权限 / 复杂 RBAC —— MVP 不做。

---

## License

私有，未授权。
