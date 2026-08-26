# vplatform —— vibe-niuma v2

企业级并行 AI 开发平台。设计文档：
[`docs/superpowers/specs/2026-08-24-v2-parallel-platform-architecture.md`](../docs/superpowers/specs/2026-08-24-v2-parallel-platform-architecture.md)

> **一句话**：每个项目是一个空间；所有对它有需求的人只提需求；每条需求在隔离环境里
> 并行跑完「澄清 → 拆解 → 实现 → 验证 → 复核 → 预览 → 审核 → 合并 → 上线」；
> 合并期的冲突由 AI 解决。

## 分层

```
core/          数据模型 · 配置 · 事件总线（MySQL 持久化 + Redis 实时）
orchestration/ jobs/steps/signals · 分级轮询 worker · 声明式 DAG 引擎
workspace/     git worktree + 容器隔离 · 端口租约 · 依赖预烘焙
agents/        AgentSession（opencode CLI --session / server API）
review/        CodeReviewAdapter（ocr）+ 自建过滤合并层
merge/         合并队列（per-repo 串行）· 三档冲突处理
deploy/        DeployAdapter · 三层环境
hosts/         GitHostAdapter（只实现 GitHub，接缝已立）
skills/        Skill 层三层安装
api/           FastAPI
```

## 跑起来

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev,redis]"

# skill dist（流水线各环节的实现）
(cd ../platform-skills && ./build.sh)

.venv/bin/python -m pytest -q          # 100 passed
.venv/bin/python scripts/check_seams.py

# 本地演示
VP_DATABASE_URL=sqlite:////tmp/vp.db .venv/bin/python scripts/seed_demo.py
VP_DATABASE_URL=sqlite:////tmp/vp.db .venv/bin/uvicorn vplatform.api.main:app --port 9000
```

前端在 `../web`（`npm i && npm run dev`，代理到 :9000）。

## 五条会反复用到的约定

**① 密钥只以引用进 DB。** `Project.secret_refs` 存 `"env:DASHSCOPE_API_KEY"`，
真实值由 `resolve_secret()` 从环境取。v1 把 key 明文存 `String(256)`，泄库即泄密钥。

**② 结构化输出只用 `response_format: json_object`。**
目标模型（DeepSeek / DashScope）**不支持** `json_schema`，也不支持强制
`tool_choice`（thinking 模式）。这是兼容性下限，按下限写的代码在任何端点都能跑。
见 [`core/config.py`](src/vplatform/core/config.py) 与设计文档 §6.3。

**③ handler 必须用 `ctx.session`。** 不要在 handler 里另开 `session_scope()` ——
业务状态与 job 状态是一次动作的两半，分两个事务写会在崩溃时留下不一致，
sqlite 上还会直接死锁。事件走 `ctx.emit()`（outbox：同事务落库，提交后 fan-out）。

**④ `Run` 是幂等边界。** 重试 = 新建 Run，不复用。Workspace 与 Run 1:1，Run 终结即回收。

**⑤ 核心层不得 import 具体实现。** CI 跑 `scripts/check_seams.py` 守着。
靠自觉守不住 —— v1 的「支持 GitHub / Gitee / 云效」就是这么变成空话的。

## 三条实测得出的硬约束

来自 2026-08-24 对 `ocr` v1.9.10 + deepseek-v4-pro 的真实跑测（设计文档 §9.6–9.11）：

| 约束 | 出处 |
|---|---|
| **`retry_report` 无失败时整个 key 缺失** —— 缺失=零失败，不是未知 | `review/ocr.py:parse_ocr_json` |
| **`status: complete` + 退出码 0 也可能有失败请求** —— 必须查 `failed_requests` | `ReviewResult.degraded` |
| **0 条发现 ≠ 代码干净** —— 同一 diff 三次跑出 2/0/0，UI 只能写「未发现」 | `web/src/api/board.ts:reviewSummary` |

## 与 v1 的关系

v1（`orchestrator/` + `extension/`）三个致命问题在这里都有对应解：

| v1 问题 | v2 的解 |
|---|---|
| P1 `opencode run` 一次性调用，每轮丢会话 | `agents/` —— session 是一等公民，refine 复用、拆任务 fork |
| P2 单工作树 `checkout` 切分支，并发互相 `reset --hard` | `workspace/` —— 每 Run 独立 worktree + 容器，实测 5 并行互不污染 |
| P3 `SystemConfig` 单例 = 单项目 | `core/models.py` —— Org/Project/Member 多租户，每表带 `project_id` |
