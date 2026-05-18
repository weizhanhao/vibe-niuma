# 故障排查 · 踩过的坑

> MVP 跑通过程中**每一个**导致小时级停摆的坑。每一条都有过 PR / commit，写下来是为了「下次不要再上同一个钩」。从 README 拆出来单独成文，方便检索。

---

## 部署 / 配置类

### 1. `.env` 行内 `#` 注释被 systemd 当成 value 字面量

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

### 2. systemd 不传 `HOME`，opencode rc=1 空 stderr

**症状**：`/init CLI 非 0 退出 (rc=1)`，stderr 完全空白。手动 ssh 登录跑 `opencode run "..."` 又完全正常。
**根因**：systemd 默认不设 `HOME`；opencode 读 `~/.config/opencode/`，找不到就直接退出且不打错。
**修**：systemd unit 显式 `Environment=HOME=/root`（见 `deploy/systemd/vibe-niuma-orchestrator.service`）。

### 3. 嵌套 heredoc 同名 `EOF` 提前关掉外层

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

### 4. `rsync --delete` 没排 `.git`，每次 deploy 擦光 demo git 历史

**症状**：用户在扩展点「确认合并」后 sidebar 显示「合并成功」，回 `:5199` 刷新页面还是老 UI；ECS `cd /opt/vibe-niuma/demo && git log` 只有「demo init」一条 commit，所有 cr/ 分支消失。
**根因**：本机 `demo/` 没有自己的 `.git/`（它是 vibe-niuma 主仓的子目录），ECS 的 `.git/` 是 orchestrator pipeline 自己 init 的。`rsync --delete` 会把目的端有、源端没有的文件全删掉 → ECS 的 `.git/` 被擦 → 接着 deploy 脚本「无 .git 就 git init」重建空 repo → 历史全丢。
**修**：`rsync` 加 `--exclude '.git' --exclude 'AGENTS.md'`，并把 `--delete` 改成 `--update`。

### 5. LiteLLM `load_dotenv()` 向上爬，捞到 orchestrator 的 `DATABASE_URL`

**症状**：`vibe-niuma-llm-proxy` 启动时 prisma crash，报数据库连接错。
**根因**：LiteLLM 启动会 `load_dotenv()` 从 CWD 一路向上找 `.env`，会找到 `/opt/vibe-niuma/.env` 里给 orchestrator 用的 `DATABASE_URL`，触发 prisma 初始化。
**修**：在 LiteLLM 工作目录单独放一份只含 provider key 的 `.env`（deploy.sh 自动生成），并在 systemd unit 用 `ExecStart=/usr/bin/env -u DATABASE_URL ...`。

---

## Pipeline / Orchestrator 类

### 6. `Pipeline.run()` 只 catch `_PhaseError`，git / DB 异常被吞掉

**症状**：CR 卡在 `located` 状态不动，扩展端无任何 SSE 状态更新，quota 永远不释放。
**根因**：外层 `except _PhaseError` 之外的异常默默逃逸，CR 永远不会到失败终态。
**修**：加 `except BaseException` 兜底 mark_failed + release quota + 发 FAILED status；`CancelledError` 重抛保留 asyncio 取消语义。

### 7. `_spawn` 沉默吞后台 task exception

**修**：`_spawn` 加 `task.add_done_callback`，遇到非 cancelled 异常 `logger.exception` 出去。

### 8. dev runner 启动期 30+s 完全无输出 → 看着像死了

**根因**：opencode 启动初期不打 stdout（初始化 session / 加载 prompt）。
**修**：`stream_subprocess(heartbeat_seconds=5)` —— 子进程超过 5s 静默就发一条 `⏳ runner 静默 Xs（累计 Ys）...`。

### 9. LLM HTTP 调用全程黑盒

**根因**：`complete_vision` 用 buffered POST，等服务端完整返回才有数据。
**修**：
- `LLMClient.complete_vision_stream(on_token=)` 走 OpenAI SSE，每个 delta.content chunk 回调一次。
- `BrainstormingSkill._plan` 检测到 `channel.log` 就走流式，每 32 字符或换行 flush 一行。
- 兜底心跳：`_with_heartbeat()` wrap clarify / locate / context_pack 这些没 stdout 流的 awaitable。

### 10. merge / discard 端点不广播 SSE，扩展 mirror 卡 preview-ready

**修**：merge/discard/conflict 都补 `event_bus.publish(Event(type="status", data={"state": "merged/...", ...}))`。

### 11. `preview_url` / `branch` 是 DB 字段，不在事件里 → mirror 永远 null

**修**：preview-ready / coding 转换直接发自带 `preview_url + branch` 的 status 事件；`applyEvent('status')` 用 `??` 合并这两个字段。

### 12. `merge_to_main` 被 build 阶段的脏工作树挡住，误报 conflict

**根因**：commit_all 之后 `npm run build` 又产生新的 `dist/` 文件，工作树脏。
**修**：`merge_to_main` 进入时先 `git stash push -u`（含 untracked），merge 完 `git stash drop`。

### 13. 终态后预览容器泄漏，端口段被吃满

**修**：merge / discard 成功后 `_spawn(_teardown_preview_after_terminal)`，异步拆容器 + 释放端口。`app_state.preview` 改成 lifespan 单例，reaper / pipeline / teardown 共用同一份 `_used_ports`。

### 14. `RepoInitializer.wait_ready` 在 FAILED 也死等 timeout

**修**：`wait_ready` 头一行检查 `self._status == FAILED` 就立刻返回 `False`，不进 `wait_for`。同时把 `_INIT_WAIT_SECONDS` 从 120 → 30。

---

## 浏览器扩展类

### 15. MV3 content script 只在「扩展加载之后才打开/刷新的页」自动注入

**根因**：MV3 的「静态 content_scripts」只对加载之后才打开/刷新的 tab 生效。已经开着的 tab 里没有 content script，SW 的 `chrome.tabs.sendMessage` 静默 reject。
**修**：SW 在 `UI_START_CAPTURE` 时先 sendMessage 探测；catch 到「receiver not found」就 `chrome.scripting.executeScript({ files: <从 runtime manifest 取> })` 按需注入。
**注意**：注入路径必须从 `chrome.runtime.getManifest().content_scripts[0].js[0]` 取，**不能** hardcode `src/content/content-entry.js` —— crxjs 会把它哈希成 `assets/content-entry.ts-loader-XXX.js`。

### 16. `captureVisibleTab` 返回的 PNG 太大（5-10MB）→ POST 卡 35s+

**修**：SW 里用 `OffscreenCanvas` 把图压成 JPEG@75% / max-width 1280，典型 <300KB。

### 17. SW 30s 闲置死亡，长跑 CR SSE 断流

**修**：`chrome.alarms` 每 30s 触发一次 keepalive；alarm 触发时若有 in-flight CR 就重新 attach SSE 订阅。

---

## 数据 / 模型 / 网络类

### 18. MySQL `TEXT` 64KB 装不下截图 base64

**修**：`models.py` 用 `Text().with_variant(LONGTEXT, "mysql")`。`fail_log` 同款修。

### 19. 预览容器只起前端 → 页面没数据

**根因**：`DockerPreviewAdapter` 只 `docker build frontend/`，没起 backend；vite proxy 把 `/api` 转给 `localhost:8000`，预览容器里啥都没有。
**修**：预览容器 `--network vibe-niuma-net` + `-e VITE_API_URL=http://vibe-niuma-demo-backend:8000`，vite dev server 把 `/api` 反代到 main demo 后端，复用 `demo` schema 真数据。

### 20. 合并后 main demo 容器还跑老镜像

**修**：merge 成功后 `_spawn(_refresh_main_demo)`，异步跑 `deploy/main-demo.sh --rebuild`，stdout 行级回灌到 SSE log。脚本路径走 `MAIN_DEMO_REFRESH_SCRIPT` env（空串则跳过 = 本地开发）。

### 21. claude-code CLI 跟 DeepSeek thinking mode 协议不兼容

**症状**：claude-code 调 `deepseek-*` 系列 400 拒收（缺 `reasoning_content`）。
**修**：默认 `DEV_RUNNER=opencode`。claude-code 留着兼容路径，但需要 Anthropic 真 key + Claude 系列模型才稳。
