# vibe-niuma v2 · 企业级并行开发平台架构设计

> 状态：**设计中**（2026-08-24 起草）
> 定位：v1（浏览器扩展 + 单工作树 + 一次性 opencode 调用）的**架构级重写**。
> 目标：从「一个业务员串行提小改动」演进为「一个企业内所有需求方并行提需求、AI 并行实现、
> 审核后自动汇流上线」的平台。

---

## 0. 一句话目标

**每个项目是一个空间；所有对该项目有需求的人只提需求；每条需求在隔离环境里并行跑完
「澄清 → 拆解 → 实现 → 验证 → 预览 → 审核 → 合并 → 集成 → 上线」这条流水线；
合并期的冲突由 AI 解决。**

---

## 1. 已定决策（ADR）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 入口形态 | **纯 Web，放弃浏览器框选** | 扩展整体废弃，只抢救 React 组件。基建最干净，推进最快 |
| D2 | 隔离粒度 | **git worktree 打底 + Docker 包一层** | worktree 秒级创建共享 object store；容器隔离依赖/端口/进程。可并行装依赖、跑测试、起预览 |
| D3 | 编排引擎 | **自建 Postgres 状态机 + worker** | 不引入 Temporal/Hatchet 运维负担；接口抽象好，将来可换 |
| D4 | 数据库 | **继续用 MySQL 8，不迁 Postgres**（2026-08-24 决定） | 原提案的理由之一站不住：`FOR UPDATE SKIP LOCKED` MySQL 8.0.1 就有。其余差距（`LISTEN/NOTIFY`、部分索引、`RETURNING`、`JSONB`+GIN、事务性 DDL）都有替代，无一是硬阻塞；团队既有运维体系比这些特性更重要。补偿设计见 §7.5 |
| D5 | 配置模型 | **`SystemConfig` 单例拆成 `Project` 表**，M0 一次动完 | 与 D4 无关，照做。反正要动 schema，一次动完，避免 M1 二次迁移 |
| D6 | 需求拆解 | **AI 自动拆**（不强制人工确认） | 见 §8。内置 `touches` 冲突前置 + critic 复核两道工程保险 |
| D7 | Agent 会话 | **opencode server + session API** | v1 用 `opencode run "<prompt>"` 一次性调用，每轮丢失全部会话状态 |
| D8 | 流程定义 | **声明式 DAG（YAML in DB）** | 不再硬编码成 `pipeline.py` 那种 819 行 if-else |
| D9 | AI code review | **接 [`alibaba/open-code-review`](https://github.com/alibaba/open-code-review)（CLI `ocr`），包一层 `CodeReviewAdapter`**；不接 `opencode github` | 阿里内部跑了两年的官方 AI 审查助手，Apache-2.0，★21k。「确定性工程 × Agent 混合」架构已把我们要的四条约束全做了，且有 AACR-Bench 数据支撑（同模型下精确率/F1 显著高于通用 Agent，token 约 1/9）。见 §9 |
| D10 | CD 边界 | **vibe-niuma 管到「合进汇流分支」，之后交 `DeployAdapter`**；**只实现 `SelfHostedDeploy`**，云效 / GH Actions 只留接口不实现（2026-08-24 确认延后） | 内环（并行 agent 调度）云效给不了；外环（构建部署）云效成熟，自己造是浪费。见 §10 |
| D12 | 环节实现 | **每个流水线环节 = 一个可插拔 Agent Skill**，底座取 [`mattpocock/skills`](https://github.com/mattpocock/skills)（MIT）vendored 后改 | opencode 原生支持 Agent Skills；该 skill 集与流水线环节近乎一一对应，且 `to-tickets` 的 blocking-edges 模型独立印证了 §8 的 `depends_on`。见 §14 |
| D11 | 环境分层 | **预览 / 测试 / 生产 三层，预发暂缓** | 并行分支各自过 ≠ 合起来过，测试环境是集成回归的唯一落点。预发早期只是第二个测试环境。见 §11 |

---

## 2. v1 现状盘点

### 2.1 三个致命问题

**P1 · 没有使用 opencode 的原始会话**

`orchestrator/src/orchestrator/adapters/impl/opencode_runner.py:75`

```python
argv = [self._cli, "run", prompt, "--model", self._model]
```

一次性子进程，跑完即死。续改（refine）走
`claude_code_runner.py:_build_refine_prompt`，做法是把历史消息**拼成文本塞进新
prompt**，再让 agent 自己 `git diff` 把上一轮改动"猜"回来。

这是在手工重建 session。代价：
- agent 的 tool-call 轨迹、已读文件、推理链每轮全丢
- token 重复烧
- refine 质量天然低一档
- 跑到一半无法介入

opencode 本身提供 `POST /session` / `POST /session/:id/message` /
`POST /session/:id/prompt_async` / `POST /session/:id/fork` / `GET /global/event`(SSE)；
连 CLI 都有 `--session <id>` / `--continue` / `--fork` / `--dir`。全部没用上。

**P2 · 单工作树 + checkout 切分支 = 根本不能并行**

`orchestrator/src/orchestrator/git_manager.py:create_branch`

```python
stash push --include-untracked
reset --hard HEAD          # 还不干净就硬 reset
clean -fd
checkout <base> && checkout -b <branch>
```

整个系统只有一个工作树。pipeline 里没有任何 `Lock` / `Semaphore` / 队列。
两个 CR 并发，第二个的 `reset --hard` 会直接抹掉第一个 agent 正在写的文件。

现在没炸，只因为业务员是一个人在串行点按钮。

`multi_repo_sync.py:9` 的注释写着「每个仓一个 full clone（**不用 bare + worktree**，简化）」
—— 这条"简化"正是要推翻的那条。

**P3 · 单例配置 = 单项目**

`models.py:SystemConfig` 是 `id=1` 的 singleton，一个 `demo_repo_path`。
多项目只存在**扩展本地**（`extension/src/lib/projects.ts`），服务端不知道。
没有租户、没有成员、没有权限、没有审计。

### 2.2 其他缺口

| 缺什么 | 现状 |
|---|---|
| 编排持久化 | `asyncio.create_task` fire-and-forget，orchestrator 重启 = 在跑的需求全部蒸发 |
| 事件总线 | `events.py` 是进程内 dict + `asyncio.Queue`，单进程绑死，无法多副本 |
| 审核环节 | 状态机 `created→clarifying→located→coding→building→preview-ready→merged`，**没有 review/approve 态** |
| 冲突处理 | `merge_to_target` = `rebase + merge --ff-only`，冲突直接抛 `GitConflictError` 给人。零 AI 参与 |
| 需求关系 | 需求之间无依赖模型，无合并队列 |

### 2.3 复用清单

**保留并演进**

| 模块 | 用途 |
|---|---|
| `adapters/interfaces.py` | 4 个 Protocol，抽象方向正确，扩展后继续用 |
| `github_client.py` / `target_branch.py` / `auto_pr.py` | GitHub 操作层，基本可直接搬 |
| `multi_repo.py` / `multi_repo_sync.py` | 多仓发现 + 原子合并雏形，扩成 workspace 层的多仓支持 |
| `self_heal.py` | 失败 → LLM 决策 retry / retry_with_revised_prompt / escalate |
| `compaction.py` | 上下文超长自动 summarize |
| `intent_classifier.py` | new / refine / chat 分流 |
| `brainstorming_skill.py` | 澄清逻辑（需解开与"框选截图"的耦合） |
| `events.py` 的 buffer + replay **语义** | 设计正确，只换传输实现 |
| `docker_preview.py` 的端口段 + TTL 回收**思路** | 改造成 per-workspace |
| extension 的 React 组件 | `ChatStream` / `ChatInputBar` / `PreviewDock` / `ConversationList` / `HelpBubble` 搬进 web，剥掉 `chrome.*` |

**废弃**

- `extension/` 整体（D1）——只抢救上表组件
- `demo/`（玩具应用）
- `pipeline.py` 的 819 行硬编码流程（D8 取代）
- `events.py` 的进程内实现（§7 取代）
- `models.py:SystemConfig`（D5 取代）

---

## 3. 目标架构分层

```
┌─────────────────────────────────────────────────────────┐
│  Web 工作台 (React)                                      │
│  空间 / 需求池 / 看板 / 会话 / 预览 / 审核 / 合并队列      │
└───────────────────────┬─────────────────────────────────┘
                        │ REST + SSE(WS)
┌───────────────────────▼─────────────────────────────────┐
│  API 层 (FastAPI)  ── 租户鉴权 · 配额 · 审计              │
├─────────────────────────────────────────────────────────┤
│  ⑤ 编排层    Postgres 状态机 + worker pool + 人工 gate    │
│              声明式 DAG 引擎                              │
├─────────────────────────────────────────────────────────┤
│  ④ 事件总线  Postgres LISTEN/NOTIFY + events 表(可回放)   │
├──────────────────────┬──────────────────────────────────┤
│  ③ AgentSession 层    │  ② Workspace 隔离层               │
│  opencode serve       │  bare mirror + worktree + 容器    │
│  create/send/fork/    │  acquire / release / exec        │
│  resume/stream        │  端口租约 · 配额 · GC             │
├──────────────────────┴──────────────────────────────────┤
│  ① 数据模型 (PostgreSQL) —— 全表 project_id 隔离          │
└─────────────────────────────────────────────────────────┘
```

**这五层不打完不碰业务功能。**

---

## 4. 数据模型

```
Org
└── Project(空间)
    ├── Repo[]              一个空间可绑 N 个仓（前端 + 后端 + ...）
    ├── Member[]            role: requester / reviewer / admin
    ├── Pipeline            声明式 DAG 配置
    └── Requirement[]       一条需求 = 一个隔离单元
        ├── Task[]          AI 拆出的并行子任务（DAG）
        │   └── Run[]       一次执行 —— 幂等边界，重试即新建
        │       ├── Workspace     worktree + container，与 Run 1:1
        │       ├── AgentSession  opencode session_id，跨 Run 可 resume/fork
        │       └── Event[]
        ├── Review[]        approve / reject / comment
        └── MergeJob        合并队列条目
```

### 4.1 硬约束

- **每张业务表带 `project_id`**，租户隔离落在 schema + 索引层，不靠应用层记得加 `WHERE`
- **`Run` 是幂等边界**。重试 = 新建 Run，不复用旧 Run。Workspace 与 Run 1:1，Run 终结即回收 Workspace
- **`AgentSession` 独立于 Run 存在**。这是 P1 的落点：
  - 同一 Requirement 的第二轮 refine → 复用 session_id 走 `resume`
  - 拆出并行子任务 → 从父 session `fork`

### 4.2 M0 迁移范围（一次动完）

留在 MySQL 8（D4），所以 M0 **没有跨库迁移**，只有 schema 变更。
大文本字段沿用 v1 已有的 `Text().with_variant(LONGTEXT, "mysql")` 写法。

```sql
-- 新增
CREATE TABLE orgs (...);
CREATE TABLE projects (              -- 取代 SystemConfig 单例
  id, org_id, name, slug,
  dev_runner, dev_model, vision_model,
  workspaces_root, target_branch,
  quota_parallel_runs, quota_ports,
  config jsonb,                      -- 密钥引用（不明文存 key）
  version, created_at, updated_at
);
CREATE TABLE project_repos (id, project_id, name, url, default_branch, target_branch, pat_ref);
CREATE TABLE members (id, project_id, user_id, role);
CREATE TABLE agent_sessions (id, project_id, requirement_id, provider, session_id, parent_session_id, created_at);

-- 迁移
change_requests → requirements（保留历史；state 值映射到新状态机）
conversation    → requirements.thread（或独立 threads 表）

-- 删除
system_config
```

**密钥不再明文入库**：`projects.config` 只存引用，实际值走环境/密钥管理。v1 的
`system_config.deepseek_api_key` 等字段直接明文存 String(256)，M0 一并修掉。

---

## 5. Workspace 隔离层（最难的一块）

### 5.1 磁盘布局

```
/data/projects/<project_id>/
  mirrors/<repo>.git          bare mirror —— 共享 object store，只 fetch 一次
  workspaces/<run_id>/
    <repo_a>/                 git worktree add，秒级
    <repo_b>/
```

容器 bind-mount `workspaces/<run_id>/`，一个 Run 一个容器。

### 5.2 接口

```python
class WorkspaceProvider(Protocol):
    async def acquire(self, run: Run) -> Workspace:   # worktree × N 仓 + 容器 + 端口
    async def release(self, ws: Workspace) -> None:   # 容器停 + worktree remove + 端口归还
    async def exec(self, ws: Workspace, argv: list[str]) -> ExecResult
```

首实现 `WorktreeDockerProvider`。将来 `K8sProvider` 不动上层。

### 5.3 三个必须现在解决的坑

**坑 1 · 依赖安装是并行的真瓶颈**

worktree 秒级创建，但每个 workspace 跑一次 `npm i`（3 分钟）就白搭了。

解法：
- 项目级**预烘焙镜像** `project:<id>:deps`，锁文件 hash 变了才重建
- pnpm / uv store 以只读卷共享挂载
- workspace 启动时依赖已就位，只做增量

**这一条不做，"并行"只是名义上的。**

**坑 2 · 端口分配**

v1 `docker_preview.py` 硬编码全局 `5100-5199`。改为：
- `port_leases` 表（`project_id, port, run_id, leased_at, expires_at`），唯一索引防重
- per-project 端口配额
- TTL 过期 + Run 终结双路回收

**坑 3 · 多仓原子性**

一个需求同时改 frontend + backend：
- `acquire` 要为 N 个仓各起一个 worktree
- 合并时 N 仓要么全成要么全滚 —— 扩展 `multi_repo.py:merge_to_main_atomic`
- 任一仓验证失败 → 整个 Run 失败

---

## 6. AgentSession 层

### 6.1 M0 止血（改动约 10 行）

```python
argv = [cli, "run", prompt,
        "--session", session_id,      # ← 关键
        "--dir", ws.path,             # ← 配合 worktree
        "--model", model]
```

`session_id` 落 `agent_sessions` 表，refine 时带上。
`_build_refine_prompt` 那套「拼历史 + 让 agent 自己 git diff」当场作废。

### 6.2 M3 正解

每个 Workspace 容器内跑一个 `opencode serve`，orchestrator 走 HTTP：

| 场景 | 调用 |
|---|---|
| 新需求 | `POST /session` → 存 session_id |
| refine 续改 | `POST /session/:id/message` |
| 拆并行子任务 | `POST /session/:id/fork` × N |
| 长任务不阻塞 | `POST /session/:id/prompt_async` |
| 实时日志 | `GET /global/event` (SSE) → 转发进事件总线 |

抽象成 `AgentSession` Protocol；换 claude-code / codex 只换实现。

```python
class AgentSession(Protocol):
    async def create(self, ws: Workspace, *, title: str) -> str
    async def send(self, sid: str, message: Message) -> None
    async def fork(self, sid: str) -> str
    async def stream(self, sid: str) -> AsyncIterator[AgentEvent]
```

### 6.3 模型端点能力约束（2026-08-24 实测）

**这是平台级约束，不只影响 §9 的 code review。**

对 `api.deepseek.com` 直连实测：

| 能力 | deepseek-v4-pro | deepseek-v4-flash |
|---|---|---|
| `response_format: {type: json_schema}` | ✗ `This response_format type is unavailable now` | ✗ |
| 强制 `tool_choice`（具名函数 / `"required"`） | ✗ `Thinking mode does not support this tool_choice` | ✗ 同样 |
| `tools` + `tool_choice: "auto"` | ✓ 但模型可能不调 | ✓ |
| **`response_format: {type: json_object}`** | **✓ 稳定** | ✓ |

`json_object` 要求 prompt 中出现 "json" 字样，否则报
`Prompt must contain the word 'json'`。

### 影响范围

**凡是需要结构化输出的环节都受此约束**，不能依赖 forced `tool_choice` 或 `json_schema`：

| 环节 | 需要的结构化输出 |
|---|---|
| §8 拆解 | `tasks[]` + `touches` + `contracts` |
| §8.3 `decompose-critic` | pass/fail 裁决 |
| §9 code review 过滤 | keep/drop 裁决 |
| `diagnosing-bugs` | retry / escalate 决策 |
| `triage` | 分诊分类 |

### 平台规约

> **结构化输出一律走 `response_format: {type: json_object}` + prompt 内声明 schema
> + 我们自己做 schema 校验与重试。**
>
> 不使用 `json_schema`，不使用强制 `tool_choice` —— 它们在目标模型上不可用，
> 且失败方式是 HTTP 400，容易被上层当成偶发错误吞掉（§9.7 ② 就是这么发生的）。

换模型端点（如 §9.10 的 DashScope 方案）时，这条规约仍然成立 —— 它是**兼容性下限**，
不是最优解。按下限写的代码在任何端点上都能跑。

---

## 7. 编排层

### 7.1 表结构

```sql
jobs (
  id, project_id, run_id, type, state,
  payload jsonb,
  idempotency_key unique,
  attempts, next_run_at,
  locked_by, locked_at
);
steps (id, job_id, name, seq, state, input jsonb, output jsonb);
signals (id, job_id, name, payload jsonb, created_at);   -- 人工 gate 唤醒
```

### 7.2 worker 取任务（MySQL 8）

MySQL 没有 `UPDATE ... RETURNING`，所以是事务里三步，不是一步：

```sql
START TRANSACTION;
  SELECT id FROM jobs
   WHERE state = 'pending' AND next_run_at <= NOW()
   ORDER BY next_run_at
   LIMIT 1
   FOR UPDATE SKIP LOCKED;          -- MySQL 8.0.1+ 支持
  UPDATE jobs
     SET state = 'running', locked_by = ?, locked_at = NOW()
   WHERE id = ?;
COMMIT;
-- 再按 id 读回整行
```

索引：`(state, next_run_at)` 复合索引。MySQL 没有部分索引，热区靠 §7.5 的冷热分表解决。

### 7.3 必须满足的四条

1. **幂等** —— 每个 step 的 output 落库；重放时命中即跳过（Temporal replay 的最小自建版）
2. **可恢复** —— 进程被 kill，`locked_at` 超时后另一 worker 接管，从最后一个完成的 step 续跑
3. **人工 gate** —— 审核态 = `state='awaiting_signal'`，**不占 worker**。人点 approve → 写 `signals` 表 + 把该 job 的 `next_run_at` 置为 `NOW()`，下一轮轮询捡到（延迟上限 = 轮询间隔，见 §7.5）
4. **并发控制** —— per-project 并行 Run 上限；per-repo 合并串行

### 7.4 声明式 DAG

```yaml
pipeline:
  - triage:    {skill: triage}                                   # §14
  - clarify:   {skill: grilling, gate: auto}
  - decompose: {skill: to-tickets, critic: decompose-critic, output: tasks[]}
  - implement: {parallel: tasks, skill: tdd, workspace: required}
  - verify:    {run: [lint, test, build],
                on_failure: {skill: diagnosing-bugs, max_attempts: 2}}
  - ai_review: {adapter: ocr, block_on: [critical],               # §9 缺陷轴
                plus_skill: code-review}                          # §14 规格轴 + 规范轴
  - preview:   {expose: true, env: preview}                      # §11
  - review:    {gate: human, approvers: 1}
  - merge:     {queue: per-repo,                                  # §12
                conflict: [git, mergiraf, {skill: resolving-merge-conflicts}]}
  - deploy_test: {adapter: deploy, env: test}                    # §10
  - integrate: {run: [e2e], env: test}
  - release:   {gate: human, adapter: deploy, env: prod}
```

引擎读 DAG 执行。**加环节 = 改 YAML，不改代码。**

### 7.5 MySQL 上的四处补偿设计（D4 的直接后果）

MySQL 8 缺的四样东西各有对策。**这些不是将就，其中两条比 Postgres 原生方案更好。**

**① 没有 `LISTEN/NOTIFY` → 分级轮询 + 退避**

不要为了低延迟把轮询压到 50ms，那是白烧 DB。按队列分两级：

| 队列 | 轮询间隔 | 场景 |
|---|---|---|
| 交互类 | 200ms | 人刚点了「通过」「重试」「回答澄清」—— 要秒回 |
| 后台类 | 2s，空转指数退避到 5s | agent 跑代码、构建、部署 —— 本来就是分钟级 |

人工 gate 的唤醒延迟上限 = 200ms，人完全无感。

**② 没有部分索引 → 冷热分表（比部分索引更好）**

`jobs` 表只留活跃行；终态行由 reaper 定期搬到 `jobs_archive`。

Postgres 的部分索引只解决索引大小，表本身照样膨胀；分表同时解决了两个问题。
**这条 MySQL 反而逼出了更好的设计。**

**③ 没有 `JSONB` + GIN → `touches` 用关联表（比 JSON 数组更好）**

§8 的冲突检测要查「哪些 in-flight 需求的 touches 与本任务相交」。

不要存 JSON 数组，规范化成关联表：

```sql
CREATE TABLE task_touches (
  task_id  VARCHAR(36),
  path     VARCHAR(512),
  PRIMARY KEY (task_id, path),
  KEY idx_path (path)          -- 交集查询就是普通 JOIN
);
```

**这条也是 MySQL 逼出的更规范模型** —— 即使在 Postgres 上，这个设计也比 `text[] && GIN` 好。
`jobs.payload` 之类只存取、不做索引查询的，继续用 MySQL 的 `JSON` 类型。

**④ 没有事务性 DDL → 迁移纪律**

Alembic 每个 migration **只做一个 DDL 操作**，失败可手工从断点续。
早期数据小直接 `ALTER`；将来大表变更再上 gh-ost / pt-online-schema-change。

---

## 8. AI 需求拆解（D6）

### 8.1 输入

- 需求原文 + 澄清问答
- 仓库结构 / 项目知识（复用 `brainstorming_skill` 的 repo doc 注入）
- **当前 in-flight 的其他需求的 `touches` 集合**（用于跨需求冲突预测）

### 8.2 输出契约

```json
{
  "tasks": [
    {
      "id": "t1",
      "title": "订单列表加筛选器",
      "repos": ["frontend"],
      "touches": ["src/pages/Orders.tsx", "src/api/orders.ts"],
      "depends_on": []
    },
    {
      "id": "t2",
      "title": "订单查询接口支持 status 参数",
      "repos": ["backend"],
      "touches": ["src/routers/orders.py"],
      "depends_on": []
    }
  ],
  "contracts": [
    {"kind": "http", "spec": "GET /orders?status=<enum> → Order[]"}
  ],
  "risk_notes": "t1 依赖 t2 的接口契约，已通过 contracts 解耦，可并行"
}
```

### 8.3 两道工程保险（不是人工确认，是自动化的）

**保险 1 · `touches` 冲突前置**

强制 AI 声明每个 task 预计触达的文件/模块，调度器据此：
- **同需求内** task 的 `touches` 有交集 → 不并行，串行或合并为一个 task
- **跨需求** `touches` 与 in-flight 需求有交集 → 标记高冲突风险，合并队列优先级排序 + 提前预警

**把冲突预防前置到调度期，而不是全都堆到合并期再让 AI 收拾。**

**保险 2 · `decompose_critic`**

拆解结果交给第二个 agent 复核，检查：
- 有没有漏掉的改动面
- task 之间有没有 AI 没声明的隐藏依赖
- 跨仓协作是否需要先固定接口契约

critic 不通过 → 打回重拆（最多 N 轮），仍不过 → 降级为单 task 串行执行。

**契约先行**：跨仓拆解时必须先产出 `contracts`（接口 schema），前后端 task 各自基于契约并行开发。这是并行开发的经典解法，也是 `touches` 不重叠的前提。

### 8.4 wide refactor 例外 —— 不是所有需求都能垂直切片

来自 `to-tickets`（§14）的关键补充，原设计漏掉了这一类：

**wide refactor** = 一次机械改动（重命名一个列、改一个共享类型），其 **blast radius
扇出到全仓** —— 一次编辑同时打断几千个调用点，**任何垂直切片都无法单独变绿**。

不要硬塞成 tracer bullet，改走 **expand–contract**：

```
expand    新形式与旧形式并存，什么都不破
  ↓
migrate   按 blast radius 分批（每包 / 每目录）迁调用点，
          每批一个 task、被 expand 阻塞；旧形式还在，所以 CI 批批都绿
  ↓
contract  删掉旧形式，被每一个 migrate 批次阻塞
```

批次本身也无法单独变绿时，保留该序列但让它们共用一条集成分支，全部阻塞一个
最终的 integrate-and-verify task —— 只在那里承诺绿。

**为什么这条对并行平台格外重要**：wide refactor 的 `touches` 几乎与所有 in-flight
需求相交，§8.3 保险 ① 会把它标成高风险并卡住。**但正确处理不是卡住，是识别成
wide refactor 并转 expand–contract 序列。** 拆解 agent 必须能区分这两类，
`decompose-critic` 的检查项要包含这一条。

### 8.5 逃生阀

拆解结果落库并在 Web UI 可见**可编辑**。人**可以**改，但不**必须**改 —— 不阻塞流程（符合 D6）。

---

## 9. AI Code Review（D9）

放在 `verify` 之后、人工 `review` 之前。**接入 [`alibaba/open-code-review`](https://github.com/alibaba/open-code-review)（CLI 名 `ocr`），不自建、不接 `opencode github`。**

### 9.1 为什么是 OCR

阿里集团内部官方 AI 代码审查助手，两年服务数万开发者、识别数百万缺陷后开源。
Apache-2.0，Go，★21k，高频迭代中。

核心架构「**确定性工程 × Agent 混合**」：文件筛选、文件打包（关联文件归并为同一
审查单元，各自作为上下文隔离的 sub-agent，天然支持并发）、规则匹配、评论定位与反思
这些"不能出错"的环节由工程逻辑保证；只把动态决策与上下文召回交给 Agent。

它已经把本节原先自建方案的四条约束全部实现：

| 设计约束 | OCR 的实现 |
|---|---|
| ① reviewer 必须是全新会话，不带写代码时的记忆 | 独立进程，天然满足 |
| ② 跑在 workspace 里，能读全仓而非只看 diff | `--repo <worktree>`；Agent 具备读全文件 / 搜代码库 / 查其他变更文件的工具 |
| ③ 输出结构化 | `--format json\|sarif`，severity ∈ critical / high / medium / low |
| ④ 加一道证伪降噪 | **默认内置 LLM post-filter**（`--no-filter` 用于关闭它） |
| ⑤ diff-aware，只报本次触及的行 | `--from` / `--to` / `--commit` 原生 |

基准：AACR-Bench（50 个热门仓库 / 200 个真实 PR / 10 种语言 / 80+ 资深工程师交叉标注
1505 个缺陷）。同底层模型下精确率与 F1 显著高于通用 Agent，token 约 1/9，更快。

### 9.2 为什么不是 `opencode github`

opencode 官方集成（GitHub App + Action，评论 `/opencode` 触发）的三条假设与 v2 冲突：

| 它假设 | v2 实际 |
|---|---|
| review 发生在 GitHub PR 上 | review gate 在平台自己的审核页 |
| 跑在 GitHub Actions runner | workspace + 容器已在自有基础设施，再跑一遍是重复 |
| 绑死 GitHub | 需兼容云效 Codeup / Gitee |

（GitHub 上的 `opencode-review*` 社区项目最大 ★66，与本节无关。）

### 9.3 接入方式

```bash
ocr review \
  --repo    /data/projects/<project>/workspaces/<run_id>/<repo> \
  --from    vibe/dev --to cr/<req>-<task> \
  --background-file requirement.md \
  --rule    project-rules.json \
  --format json --audience agent \
  --max-tokens-budget 200000 \
  --concurrency 8
```

关键参数：

| 参数 | 用途 |
|---|---|
| `--background` / `--background-file` | **质量杠杆** —— 把需求原文 + 澄清问答 + §8 产出的 `contracts` 组装成 Markdown 喂进去，reviewer 才能审「有没有做到需求」，而不只是代码味道 |
| `--rule` | 每个 Project 一份规则 JSON，含路径过滤 / 指定路径 |
| `--max-tokens-budget` | 成本硬顶。超出后停止派发、发布部分结果并 exit 0；只有全部条目都失败才非 0 |
| `--resume <session-id>` | 断点续跑，接得上 §7 的幂等重试 |
| `--preview` | 只看会审哪些文件，不烧 token —— 调规则时用 |
| `--concurrency` | 默认 8，与 §5 的 workspace 配额一起限 |

集成形态是 subprocess 调 CLI，与现有 `_dev_runner_common.stream_subprocess`
（并行 drain stdout/stderr + 心跳）完全同构，直接复用。

### 9.4 三条必须记住的限制

**① 它用召回率换精确率 —— 这是明示的设计取舍**

README 直说 recall 低于通用 Agent。对「人工审核 gate 前降噪」这个场景是正确取舍，
但意味着 **OCR 不是安全审计工具**，不能指望它抓全所有漏洞。
`severity: critical → 自动打回` 的规则必须基于这个认知，安全审计要另外安排。

**② 项目很新，必须 pin 版本**

2026-05-18 创建，三个月 ★21k，迭代频繁。锁定具体 release，禁止 `latest`。

**③ 委托模式要小心**

`ocr delegate` 让 OCR 只做文件筛选 + 规则解析，由自己的 agent 跑 LLM（省一套 API key）。
但**委托给写代码那个 session 会破坏「新会话」前提**（§9.1 约束 ①），必须单独 fork。

我们已有 LiteLLM proxy 且 OCR 兼容 OpenAI / Anthropic 端点，直接指过去用默认模式更省事。

### 9.5 我们仍要自己做的

1. **`CodeReviewAdapter` Protocol** —— 包住 OCR，保持可换（同 D10 的接缝纪律）
   ```python
   class CodeReviewAdapter(Protocol):
       async def review(self, ws: Workspace, *, base: str, head: str,
                        background: str, rules_path: str | None) -> list[Finding]
   ```
2. **组装 `--background`** —— 需求原文 + 澄清问答 + `contracts` → Markdown
3. **findings 落库 + 审核页渲染 + 「让 agent 直接修」回路** —— OCR 只出结论，工作流是我们的
4. **成本配额** —— per-run `--max-tokens-budget` + per-project 累计上限

**默认非阻塞**：结论作为审核页上的一块参考；只有 `critical` 才自动打回给 coder。

### 9.6 实测结果（2026-08-24，v1.9.10 + deepseek-v4-pro 直连）

在本仓两个真实 commit 上跑过。**结论：OCR 可用，但 DeepSeek 直连不能用作它的模型端点。**

| | 跑 1 · `dee3b01` 跨栈 | 跑 2 · `55095ff` 纯 Python |
|---|---|---|
| 输入 | 3 文件 / 375 行（TSX + Python） | 2 文件 / 369 行 |
| 耗时 | 3m48s | 7m26s |
| token | 142,900（入 114k / 出 29k / 缓存命中 80k） | 103,114 |
| 发现 | 2 条 | 3 条 |
| **失败请求** | 1 / 15 | **3 / 10** |

**质量抽检（人工核对源码）**

跑 2 的两条 `medium/bug` 属实：`alert.py:96` 是 `except httpx.HTTPError`，
而 `json.JSONDecodeError` 是 `ValueError` 子类**捕不到** —— webhook 返回 2xx 但正文非
JSON 时，解码错误逃出模块第 10 行自述的契约「失败统一抛 AlertError」。

**更能说明问题的是它没有对 Discord 分支误报** —— 因为那段根本不调 `r.json()`。
这是真在读代码，不是按模板对三个 provider 一起开火。

Python 覆盖没有问题；跑 1 的 Python 零发现只是那段代码确实干净。

### 9.7 实测挖出的三个硬问题

**① DeepSeek 直连不可用 —— 降噪过滤 100% 静默失效**

`review_filter_task`（§9.1 表格里的约束 ④，OCR 的内置降噪）每次都收 HTTP 400。
curl 直接验证根因：

| DeepSeek `/chat/completions` | 结果 |
|---|---|
| `response_format: {type: json_schema}` | ✗ `This response_format type is unavailable now` |
| `response_format: {type: json_object}` | ✓（但要求 prompt 中含 "json" 字样） |
| `tools` / function calling | ✓ |

OCR 的过滤任务需要严格结构化输出，DeepSeek 不提供。
**后果：所有发现都是未过滤的原始输出**，§9.1 声称的降噪并未发生。

**处置：三层方案，第一层已验证可用（见 §9.10）。**

底层原因见 §6.3 —— 这是平台级的模型能力约束，不是 OCR 独有的问题。

**② 失败是静默的 —— 不能信 `status: complete`**

跑 2 有 3/10 请求失败，但输出里 `status: "complete"`、进程退出码 0、
`manifest.coverage.failed: []`。**唯一痕迹埋在 `retry_report` 里。**

> **`CodeReviewAdapter` 必须检查 `retry_report.failed_requests`，
> `> 0` 一律视为降级运行并上报**，不得只看 `status` 与退出码。

**③ 失败不重试**

`retry_report` 这套基础设施存在，但实测失败项的 `attempts` 数组长度都是 1。
provider 类 400 不重试合理（重试也会再 400），但 `plan_task` 那条
`error_class: timeout / failure_phase: context` 是该重试的。

**④ 默认路径规则对语言栈不对称**

`--preview` 显示它按 `default_path` 规则排除了
`extension/tests/*.test.tsx`，却**保留**了 `orchestrator/tests/*.py`。

不过跑 2 对测试文件的那条发现（测试传了 `link_url` 却没断言它出现在 payload 里）
是有价值的。**所以「要不要排除测试文件」是规则配置决策，不是 bug** ——
但两种栈必须一致，需在 `--rule` 里显式规定。

### 9.8 成本与时延（实测推算）

- **成本**：约 280–380 token / 改动行。一条改 2000 行的需求 ≈ 60–80 万 token。
  **`--max-tokens-budget` 是必需项，不是可选项**，且要有 per-project 累计配额
- **时延**：3–7 分钟（concurrency 4）。放在 `verify` 之后、人工 `review` 之前，
  可与预览容器构建并行，可接受

### 9.10 端点问题的解决方案（三层，按优先级）

#### 第一层 · 自建过滤合并层（**已实测跑通**）

**关键前提：OCR 的过滤失败是 fail-open。** 实测证明 —— `review_filter_task` 收 400 后，
5 条发现照样全部输出。所以最坏情况是**噪音多，不是漏报**。

而我们**本来就需要这一层** —— OCR 的缺陷轴与 `code-review` 的规格轴要合并去重，
那就是同一个落点，顺手把过滤做掉。

实测（2026-08-24，deepseek-v4-pro + `response_format: json_object`，
输入为 §9.6 两跑产出的 5 条未过滤发现）：

| 裁决 | 发现 | 理由 |
|---|---|---|
| 丢弃 | 硬编码 endpoint 路径（maintainability） | 纯维护性建议，无具体失败场景 |
| **保留** | `webhookUrl` 未 trim（bug/low） | 空白串绕过禁用逻辑，提交时才失败 |
| 丢弃 | 测试未断言 `link_url`（test/low） | 测试改进，非实际缺陷 |
| **保留** | `alert.py:90` JSONDecodeError（bug/medium） | 破坏 `AlertError` 契约 |
| **保留** | `alert.py:117` 同上 | 同上 |

**3 条真 bug 全留、2 条弱发现全丢、置信度全 `high`。**
成本 5,057 token（约 1k / 条）—— 相对两跑 review 本身的 246k token 是零头。

> 「测试未断言」那条被丢是**策略决定不是错误**：判据写的是「没有具体失败场景就丢」。
> 这个旋钮现在在我们手上，可调。

**这一层的真正价值：把「端点能力」从 blocking 降级成优化项。**
就算 OCR 内置过滤永远坏着，我们也能产出干净结果。

#### 第二层 · 换端点到 DashScope（**推荐，key 已在手**）

`dashscope` 是 OCR 内置 provider
（`https://dashscope.aliyuncs.com/compatible-mode/v1`），供应同一个
`deepseek-v4-pro`；LiteLLM 的模型元数据标注
`dashscope/deepseek-v4-pro → supports_response_schema: True`。

**DashScope key 已经在生产服务器上了**，不用新申请。v1 的视觉链路就是走它：

```
orchestrator ──► LiteLLM proxy :8787 ──► dashscope/qwen-vl-plus
   (_llm.py:52 用 anthropic_base_url)      (api_key: os.environ/DASHSCOPE_API_KEY)
```

见 `deploy/llm-proxy/config.example.yml`。实际的 `config.yml` 与 `deploy/.env`
都在 gitignore 里，内容在服务器上（§9.10 附注）。

**接法**（OCR 直连，**不要走 proxy**，原因见下）：

```bash
ocr config set provider dashscope
ocr config set providers.dashscope.api_key "$DASHSCOPE_API_KEY"
ocr config set model deepseek-v4-pro
ocr llm test
```

**已验证（2026-08-24）**：用生产同账号的 DashScope key 实测。

| 能力 | DeepSeek 直连 | DashScope |
|---|---|---|
| `response_format: json_schema` | ✗ `unavailable now` | **✓** |
| `response_format: json_object` | ✓ | ✓ |
| 强制 `tool_choice`（具名 / `required`） | ✗ Thinking mode | ✗ `The tool_choice parameter does not support...`（`qwen3.8-max` 同样，属**平台级**限制） |

OCR 换到 DashScope 后 **`review_filter_task` 不再 400，失败请求 0，
`retry_report` 整个 key 都不再输出**。

#### 第三层 · 走现有 LiteLLM proxy（**不推荐，有坑**）

两个理由：

**① `drop_params: true`。** `deploy/llm-proxy/config.example.yml` 的
`litellm_settings` 明确开了这个。它会把模型不认识的参数**静默丢掉** ——
包括 `response_format: json_schema`。

于是失败方式从「HTTP 400」变成「OCR 拿到自由文本、解析出垃圾」。
**后者更难发现**，因为不会报错。

（这个设置本身没错 —— 它是为了 claude-code 传 Anthropic 专属参数不翻车而加的。
但对 OCR 恰好有害。）

**② LiteLLM 对不支持的 provider 是 drop 不是翻译**，本来也解决不了问题。

若一定要走 proxy，必须给该 model 单独关掉 `drop_params`，并实测确认
`json_schema` 真的透传到了 DashScope。

#### 附注：生产服务器现状（2026-08-24 探测）

`docs/RUNBOOK.md:129` 记录的 ECS `114.55.171.64`（Alibaba Cloud Linux 4）**仍在运行**：

| 探测 | 结果 |
|---|---|
| `:9000/health` | HTTP 200，`status: ok` |
| `:5199`（main demo） | HTTP 200 |
| `:22` | 开放 |
| `uptime_seconds` | 8,385,894（≈ 97 天） |
| `last_cr_at` | **null** —— 部署至今没跑过一条 CR |
| `services.mysql / llm_proxy / main_demo` | 全是 `unknown`，健康检查探不到 |

> 两点值得注意：① 97 天零 CR，说明这套 v1 实际没被用起来；
> ② 三个下游服务健康状态都是 `unknown`，`/health` 的扩展探测（Plan 11 M3.T18）
> 在生产上没真正生效 —— v2 的健康检查不要重复这个问题。

### 9.11 端点 A/B 实测与最终配置（2026-08-24）

同一个 commit（`dee3b01`）、同一个模型名（`deepseek-v4-pro`）跑三次：

| 跑 | 端点 | 内置过滤 | 发现 | token | 耗时 |
|---|---|---|---|---|---|
| 1 | DeepSeek 直连 | 失效（400） | **2** | 142,900 | 3m48s |
| 2 | DashScope | 生效 | **0** | 250,454 | 7m31s |
| 3 | DashScope | `--no-filter` | **0** | 165,732 | 4m57s |

#### 结论 ① OCR 的召回不稳定 —— 0 条 ≠ 代码干净

跑 3 关掉过滤仍是 0 条，**证明那 2 条不是被过滤掉的，是 review 本身没找到**。

同一份 diff 得到 2 / 0 / 0。分不清是 LLM 随机性还是两个端点的
`deepseek-v4-pro` 服务配置不同（DashScope 上另有 `deepseek-v4-pro-0813` 快照，
别名可能指向别处），各 n=1 说明不了。

> **硬规约：绝不能把「OCR 报 0 条」呈现为「本次改动没有问题」。**
> UI 上要写「未发现」而不是「无问题」，且不得据此跳过人工审核。
> 这与它 README 自述的「用召回换精确」一致，但同一输入 2→0 的波动幅度必须记住。

#### 结论 ② 内置过滤的成本不划算

跑 2 减跑 3 = 内置过滤这一步花掉 **84,722 token + 2.5 分钟**。

而 §9.10 第一层的自建过滤，5 条发现只花 **5,057 token** —— **约 17 倍差距**。

#### 最终配置

**DashScope 端点 + `--no-filter` + 自建过滤层。**

| 选择 | 理由 |
|---|---|
| DashScope 端点 | 避免 §9.7 ① 那一整类静默 400；`json_schema` 可用意味着别的内部任务也不会踩坑 |
| `--no-filter` | 省 85k token / 2.5 min，拿到确定性的原始输出 |
| 自建过滤 | 17 倍便宜；判据旋钮在我们手上；**本来就要在这里合并缺陷轴与规格轴** |

> 注意这里的逻辑转折：换端点的动机从「修好内置过滤」变成了
> 「避免静默失败」—— 内置过滤最终反而不用。

#### 实现细节：`retry_report` 可能整个缺失

无失败时 OCR **不输出 `retry_report` 键**。适配器必须容忍缺失，
且把「缺失」解释为「零失败」而不是「未知」：

```python
failed = int(data.get("retry_report", {}).get("failed_requests", 0))
```

（`spikes/review-filter/filter.py:load_ocr_output` 已按此实现。）

### 9.12 上线前先跑基准

[`alibaba/aacr-bench`](https://github.com/alibaba/aacr-bench)（★209，同时在 HuggingFace
`Alibaba-Aone/aacr-bench`）已开源。**接入前先在自己的仓库上跑一轮**，确认在你们的语言
栈和代码风格上确实有效，再决定 severity 门槛怎么定。

---

## 10. CD 边界与 DeployAdapter（D10）

### 10.1 两个环

```
内环 —— vibe-niuma 必须自己做          外环 —— 标准 CI/CD，别自己造
需求 → 澄清 → 拆解 → 并行实现          汇流分支 → 构建镜像 → 部署测试环境
    → 自测 → AI review → 预览          → 集成测试 → 部署预发 → 部署生产
    → 人工审核 → 合并进汇流分支
```

内环云效给不了 —— 它没有 workspace 隔离、agent 会话、并行需求调度这些概念。
外环云效 / GitHub Actions / Jenkins 都成熟。

**边界：vibe-niuma 负责到「把改动安全地合进汇流分支」，之后交给 CD。**

### 10.2 接口

**接口现在就定死**（M1 建数据模型时连 `deploy_runs` 表一起建，避免回头迁移）：

```python
class DeployAdapter(Protocol):
    """把「一个 ref 部署到一个环境」这件事抽象掉。实现方不得向外泄漏
    自己的平台概念（pipelineId / workflow / job name 等），一律收在 config 里。"""

    async def trigger(self, *, project_id: str, env: str, ref: str,
                      meta: dict) -> str:            # → deploy_run_id（本地 ID，非平台 ID）
        ...
    async def status(self, deploy_run_id: str) -> DeployStatus:
        # DeployStatus: state ∈ {queued, running, succeeded, failed, cancelled}
        #               + external_id / external_url / started_at / finished_at
        ...
    async def logs(self, deploy_run_id: str) -> AsyncIterator[str]:
        ...
    async def cancel(self, deploy_run_id: str) -> None:
        ...
```

| 实现 | 状态 | 备注 |
|---|---|---|
| `SelfHostedDeploy` | **M7 实现** | 自建 docker compose 部署测试 / 生产 |
| `YunxiaoFlowDeploy` | **只留接口，暂不实现** | 将来：`POST /oapi/v1/flow/organizations/pipelines/{id}/runs` 触发 + 轮询，或 webhook `flow-openapi.aliyun.com/pipeline/webhook/<key>`；需服务接入点域名 + 个人访问令牌 |
| `GitHubActionsDeploy` | 只留接口，暂不实现 | 将来：`workflow_dispatch` + check run 轮询 |

### 10.3 遗留问题：代码托管平台

v1 的 UI 文案（`extension/src/ui/helpContent/repo-list.md:11`、`github-pat.md:35` 等）
宣称「支持 GitHub / Gitee / 阿里云云效」，但
`orchestrator/src/orchestrator/github_client.py:72` 的 `parse_github_url` 对非
GitHub URL 直接 `raise ValueError`。**这个承诺目前是空的。**

真要接云效 Codeup，需要与 `DeployAdapter` 并列的 `GitHostAdapter`：

```python
class GitHostAdapter(Protocol):
    async def clone(self, repo: Repo, dest: Path, *, bare: bool) -> None
    async def fetch(self, repo: Repo, work_dir: Path) -> None
    async def push(self, repo: Repo, work_dir: Path, branch: str) -> None
    async def open_change(self, repo: Repo, *, head: str, base: str,
                          title: str, body: str) -> ChangeRef   # GitHub=PR / 云效=合并请求
    async def comment(self, change: ChangeRef, body: str) -> None
    def verify_webhook(self, headers: dict, raw: bytes) -> bool
```

**M0–M7 只实现 `GitHubHost` 一种。** 但接缝现在就要立起来。

### 为什么这条要写成硬约束

v1 已经犯过一次：UI 文案（`extension/src/ui/helpContent/repo-list.md:11`、
`github-pat.md:35`）承诺「支持 GitHub / Gitee / 云效」，代码却在
`github_client.py:72` 对非 GitHub URL 直接 `raise ValueError` —— **因为根本没有接缝，
承诺无处落地。**

所以定一条可检查的规则：

> **核心层（`orchestrator/core/**`、`pipeline/**`、`workspace/**`）不得 import
> 任何 host 具体实现（`github_client` / 未来的 `codeup_client`），只能依赖
> `GitHostAdapter` Protocol。**

CI 加一条 grep 断言守住它 —— 靠自觉守不住，v1 就是证据。

---

## 11. 环境分层（D11）

在并行模型里，每条需求有自己的预览环境 —— 但那只能验证「这条需求本身对不对」。

**并行分支各自验证全过 ≠ 合起来能过。** N 条需求合进汇流分支后，第一次真正
「在一起」运行就是在测试环境。**没有测试环境，集成回归直接发生在生产上。**

| 层 | 对应分支 | 生命周期 | 回答什么问题 | 谁触发 |
|---|---|---|---|---|
| **预览** | `cr/<req>-<task>` | 临时，TTL 30min 回收 | 这条需求做对了吗 | 流水线 `preview` |
| **测试 / 集成** | `vibe/dev` | 长驻，per 空间 | 这些需求**合在一起**还对吗 | 合并队列落地后自动 |
| **生产** | `main` | 长驻 | — | `release` 人工闸门 |

### 预发（staging）暂缓

预发的真正价值在「用生产数据 / 流量回放验证」。早期没有这个能力时，预发只是第二个
测试环境 —— 纯成本。等有灰度发布 / 流量回放需求了再加第四层。

---

## 12. 合并阶段

**核心认知：并行分支各自验证全过 ≠ 合起来能过。**

```
审核通过
   ↓
进 per-repo 合并队列（串行）
   ↓
rebase 到最新 target
   ↓
有冲突？
   ├─ git 自动解 ✓
   ├─ mergiraf 结构化三方合并（tree-sitter）
   │     消掉格式 / 顺序 / import 类冲突。确定性，不会瞎编，且省 LLM 调用
   └─ 仍冲突 → AI 解冲突 agent
         在该 Run 的 workspace 内运行，携带原 AgentSession 上下文
         —— 它知道自己当初为什么这么改
   ↓
**重跑验证**（lint + test + build，这一步不能省）
   ↓
过 → push；不过 → 打回，需求回到 requester
```

后期可加 `uber/submitqueue` 式投机并行验证。**先串行，慢但对。**

---

## 13. 事件总线

保留 v1 `events.py` 的 buffer + replay **语义**（设计正确），只换传输。

因为 D4 选了 MySQL，没有 `LISTEN/NOTIFY`，这里的方案跟原提案不同：

**职责拆开 —— Redis 管实时，MySQL 管真相**

| 组件 | 负责 |
|---|---|
| `events` 表（MySQL，append-only，自增 id） | 持久化 + 回放。断线重连、事后审计都读它 |
| **Redis Streams** | 实时 fan-out。多副本 orchestrator + 多个 Web SSE 连接都订阅它 |

- Web 端 SSE / WS 带 `last_event_id`：先从 MySQL 补齐历史，再挂到 Redis Stream 上跟实时
- agent 日志是高频（每秒几十行），**纯 DB 轮询扛不住**，所以 Redis 在 M1 就要上，不能拖

> 这不是 MySQL 的将就 —— Postgres 方案到后期日志量上来了也要拆成这样，只是能晚一点拆。
> 提前拆的好处是事件总线的接口一次成型，不用中途改。

---

## 14. Skill 层 —— 环节可插拔（D12）

### 14.1 为什么是 Agent Skills

D8 把流程做成声明式 DAG，但环节**内部**做什么原本仍是硬编码 prompt。
Agent Skills 把这一层也抽出来：**stage → skill 名字写在 YAML，skill 本体是文件。**

opencode 原生支持（native `skill` 工具，`skill({name})` 调用），
`alibaba/open-code-review` 也带 `skills/` 与 `.claude-plugin/` —— 这是正在收敛的开放标准。

### 14.2 环节与 skill 的映射

底座取 [`mattpocock/skills`](https://github.com/mattpocock/skills)（MIT）：

| 环节 | skill | 取代原设计的 |
|---|---|---|
| 需求分诊 | `triage` —— 角色状态机，产出 agent-ready brief | `intent_classifier.py` |
| 澄清 | `grilling` / `grill-with-docs` | `brainstorming_skill` 的追问逻辑 |
| 产出规格 | `to-spec` | （新增能力） |
| **拆解** | **`to-tickets`** —— tracer bullet 垂直切片 + blocking edges | §8 的 planner |
| 超大需求规划 | `wayfinder` —— 一个 session 装不下的工作量 | （新增能力） |
| 实现 | `implement` / `tdd` | coder agent 的裸 prompt |
| 失败自愈 | `diagnosing-bugs` | `self_heal.py` |
| 审查·规格轴 | `code-review` —— Standards + Spec 双轴并行 sub-agent | 补 OCR 短板 |
| 解冲突 | `resolving-merge-conflicts` | §12 第三档 |
| 领域词汇 | `domain-modeling` —— CONTEXT.md + ADR | 项目知识注入 |
| 模块设计 | `codebase-design` —— deep module / seam 词汇 | （新增能力） |

> **`code-review` 与 OCR（§9）互补，不冲突**：OCR 审代码缺陷（NPE / 线程安全 /
> XSS / 注入），`code-review` 审「有没有做到规格」+「符不符合本仓规范」。两轴都跑。

### 14.3 三层安装位置

opencode 的发现顺序（项目级从 cwd 往上走到 git worktree 根）：

```
项目级  .opencode/skills/ > .claude/skills/ > .agents/skills/
全局    ~/.config/opencode/skills/ > ~/.claude/skills/ > ~/.agents/skills/
```

正好切成三层，**且默认优先级顺序就是我们要的**，不用调：

| 层 | 位置 | 装什么 |
|---|---|---|
| **L1 平台级** | 烘焙进 workspace 容器镜像的 `~/.config/opencode/skills/` | 流程环节用的 skill。**不污染客户仓库** |
| **L2 空间级** | Project 配置注入到 worktree 的 `.opencode/skills/` | 每个空间自己的规范 |
| **L3 仓库自带** | 客户仓库本来就有的 `.claude/skills/` | 天然被发现，**优先级最高** |

客户仓库自己的规范覆盖平台默认 —— 这正是想要的行为。

### 14.4 「可插拔」的三条落地规则

1. **stage → skill 的映射写在 Pipeline YAML** —— 换 skill 不动 orchestrator 代码
2. **skill 本体是文件**，三层来源，优先级明确
3. **skill 之间只经 Skill 工具互调** —— 照抄 `mattpocock/skills` 的
   `.agents/invocation.md` 纪律：禁止 `../other-skill/FILE.md` 跨目录引用，
   只能写 `Call the Skill tool with "grilling"`。**这是可插拔的封装保证**：
   换掉一个 skill 不会扯断别人

调用形态：编排层在该 stage 的 prompt 里显式写「Call the skill tool with "<name>"」。
不依赖模型自己决定要不要调 —— 环节是确定性调度的。

### 14.5 三个必须解决的冲突

**① `to-tickets` 第 4 步「Quiz the user，迭代到用户批准」违背 D6**

D6 是「AI 自动拆，不强制人工确认」。必须 fork 掉这一步，换成 §8.3 的
`decompose-critic`。MIT 协议，可改。

**② `triage` / `to-spec` / `to-tickets` 依赖「已配置的 issue tracker」**

它们默认发往 GitHub Issues / Linear，我们的 tracker 是自己的
`Requirement` / `Task` 表。

**走 `to-tickets` 的 local files 模式**：写到
`.scratch/<slug>/issues/<NN>-<slug>.md`，一个 ticket 一个文件、带 "Blocked by"，
**orchestrator 读文件入库**。比写 tracker adapter 简单得多。

**③ 版本管理 —— 不要直接依赖 upstream**

第三方 skill 会更新而我们要 fork 改。**vendored 进自己仓库 `platform-skills/`**，
定期从 upstream 同步，我们的改动单独记 patch。同 §9.4 对 OCR 的 pin 版本纪律。

### 14.6 用到的约定

- `disable-model-invocation: true` —— 编排级 skill 只能被显式调用，模型不自动触发。
  正合 DAG 引擎确定性调度的需要
- 每个 skill 带 `agents/openai.yaml`（Codex 元数据），跨 harness 可移植
- frontmatter 必填 `name`（≤64，小写字母数字连字符）+ `description`（≤1024）

---

## 15. 外部可复用项目

| 项目 | 用途 |
|---|---|
| [`anomalyco/opencode`](https://github.com/anomalyco/opencode) ★200k | server 模式 + session API，解决 P1 |
| [`dagger/container-use`](https://github.com/dagger/container-use) ★4k, Go | 多 agent 容器隔离 + 专用 git remote 的成熟范式。**参考其设计**（D2 选了自建，但坑位清单可直接抄） |
| [`BloopAI/vibe-kanban`](https://github.com/BloopAI/vibe-kanban) ★28k, Rust | web 端并行 agent 看板的交互范式。**抄形不抄码**（Rust 栈不匹配，且 2026-04 后停更） |
| [`smtg-ai/claude-squad`](https://github.com/smtg-ai/claude-squad) ★8k, Go | 多 agent 会话 + worktree 隔离的实现细节 |
| [`alibaba/open-code-review`](https://github.com/alibaba/open-code-review) ★21k, Go, Apache-2.0 | **直接接入**（D9）。AI 代码审查，确定性工程 × Agent 混合，`--format json`，见 §9 |
| [`alibaba/aacr-bench`](https://github.com/alibaba/aacr-bench) ★209 | 代码审查基准集，接入前自测用 |
| [`mattpocock/skills`](https://github.com/mattpocock/skills) MIT | **vendored 后改**（D12）。流水线各环节的 skill 底座，见 §14 |
| [`mergiraf`](https://codeberg.org/mergiraf/mergiraf) | tree-sitter 结构化三方合并，AI 解冲突的前置过滤器 |
| [`uber/submitqueue`](https://github.com/uber/submitqueue) ★199 | 投机式合并队列模型，M5 之后的优化方向 |
| [`funador/claude-code-merge-queue`](https://github.com/funador/claude-code-merge-queue) | 专为并行 coding agent 做的本地合并队列 |

---

## 16. 里程碑

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 止血 + 地基** | `opencode run --session --dir`；`SystemConfig`→`Project` 等新表（含密钥不落明文）。**不迁库**（D4）。`platform-skills/` vendoring 就位 | refine 不再重建上下文；多项目服务端可见；skill 在 opencode 里能被调起 |
| **M1 骨架** | 新数据模型 + `jobs`/`steps`/`signals`/`task_touches` + 分级轮询 worker pool + 事件总线（MySQL 持久化 + **Redis Streams** 实时，§13）。**编排层从第一天就按「stage 调 skill」写，不写硬编码 prompt 版本**（见下方注） | **杀掉 orchestrator 进程，任务能自己续跑** |
| **M2 隔离 + Skill 层** | `WorktreeDockerProvider` + 依赖预烘焙 + 端口租约 + 多仓 worktree；**L1 平台级 skill 烘焙进 workspace 镜像**（§14.3） | **5 个需求同时跑互不污染**；stage 能在 workspace 内调起 skill |
| **M3 会话** | `opencode serve` + `AgentSession` Protocol + fork | 从父 session fork 出并行子任务 |
| **M4 Web + 审查** | 空间 / 需求池 / 看板 / 会话 / 预览 / 审核 UI（抢救 extension React 组件）；**接入 `ocr`（§9）+ `code-review` 规格轴（§14.2）** | 业务员全程不碰终端；审核页能看到两轴复核结论；**`retry_report.failed_requests > 0` 时 UI 明确标「降级运行」**（§9.7 ②） |
| **M5 合并** | 合并队列 + mergiraf + `resolving-merge-conflicts` skill + 重跑验证 | 并行需求自动汇流 |
| **M6 流程** | 声明式 DAG 引擎 + **L2 空间级 / L3 仓库级 skill 注入**（§14.3） | 加环节只改 YAML；换环节实现只换 skill 文件 |
| **M7 交付** | 环境分层（预览 / 测试 / 生产）+ `SelfHostedDeploy` | 合进汇流分支后自动部署测试环境并跑集成测试 |
| ~~**M8 适配**~~ **（已延后）** | `YunxiaoFlowDeploy` + `CodeupHost` / `GiteeHost` | 2026-08-24 决定暂不做。前提已备齐：接口签名定死 + CI 守住核心层不 import 具体实现，将来只加实现文件 |

> **为什么 Skill 层从 M2 就开始，不留到 M6**
>
> D12 之前，M6 只是「把前面的硬编码抽出来」。D12 之后不成立了：若 M1–M5 先把
> 环节写成硬编码 Python，M6 再抽成 skill，等于**同一批逻辑写两遍**。
>
> 所以：**M1 的编排层直接定「stage → skill」调用契约**，M2 把 L1 平台级 skill
> 烘焙进镜像，M6 只剩 L2/L3 的注入与 DAG 配置化。
>
> 代价：M1 复杂度略升（要先定调用契约）。收益：省掉一整轮重写。
>
> 连带效果：`self_heal.py`（167 行）→ `diagnosing-bugs` skill，
> `intent_classifier.py`（183 行）→ `triage` skill，M1 少两块自建。

**M2 仍是分水岭。** 那之前系统仍然是串行的；之后才配叫「并行开发平台」。
且 M2 之后 skill 层可用，环节实现开始可插拔。

---

## 17. 未决问题

- [ ] Web 前端技术栈：React + Vite（复用 extension 组件）还是 Next.js？
- [ ] 认证方案：自建 / OIDC 接企业 IdP / GitHub OAuth？
- [ ] 预览环境暴露方式：v1 是端口段 + 公网 IP；企业级要不要走域名 + Caddy 通配泛解析？
- [ ] AI review 的 severity 门槛怎么定：OCR 是 critical/high/medium/low 四档，`critical` 自动打回，`high` 呢？（先按 §9.6 跑基准再定）
- [x] ~~验证 DashScope 是否支持 `json_schema`~~ → 已验证支持（§9.10 第二层 / §9.11）
- [ ] OCR 召回波动有多大（§9.11 结论 ①）：同一 commit 多跑几次统计，决定要不要「同一 diff 跑 N 次取并集」
- [ ] DashScope 的 `deepseek-v4-pro` 别名指向哪个快照（另有 `-0813`），与 DeepSeek 直连是否同一服务配置
- [x] ~~生产 ECS `114.55.171.64` 的处置~~ → **另起，不原地改造**。理由：
      ① v2 是重写（原样保留仅约 4%），原地改造要同时维护两套；
      ② v1 那台 97 天零 CR、数据基本是空的，没有迁移压力；
      ③ v1 留着不动当回滚兜底 —— 它是幂等的 systemd 部署，不碰就不会坏。
      v2 用 `deploy/v2/docker-compose.yml` 起在新实例或同机不同目录（端口段要错开）
- [x] ~~`--rule` 里测试文件排不排除~~ → **审，不排除**（`platform/rules/default.json`）。实测能查出「断言缺失」这类真问题；同时显式声明让 TS / Python 两栈一致
- [ ] OCR 默认模式 vs 委托模式：默认模式要给 `ocr` 单独配端点（可指向现有 LiteLLM proxy），委托模式省 key 但要单独 fork session —— 实测后定
- [ ] 测试环境的数据从哪来：脱敏生产快照 / 固定种子数据 / 每次重建？
- [ ] 合并队列落地到 `vibe/dev` 后，多久同步一次生产 `main`（定期批量 vs 每条需求单独放行）？
- [ ] Run 容器的资源限额与调度：单机 docker 到什么规模要上 K8s？
- [x] ~~「上线」环节的边界~~ → D10：管到合进汇流分支，之后交 `DeployAdapter`
- [x] ~~mergiraf 装不装~~ → **装**。它是确定性语法树合并，不调模型、不会瞎编，能把 import 顺序 / 属性顺序这类噪音冲突消掉，省下的 LLM 调用远超安装成本。未装时冲突阶梯会**如实记录跳过**（`merge/conflict.py:run_mergiraf`），不静默
- [ ] `mergiraf` 对项目实际语言的覆盖度需实测（Python / TS / TSX）
- [ ] `jobs` 冷热分表的搬迁阈值定多少（终态后 N 小时 / 表行数上限）
- [ ] `platform-skills/` 与 upstream `mattpocock/skills` 的同步节奏与 patch 管理方式
- [ ] wide refactor（§8.4）在 UI 上怎么呈现 —— 它是一条需求还是一串需求？
- [ ] （延后）云效 Codeup 的「合并请求」模型与 GitHub PR 差异有多大 —— 真要接之前必须先摸一遍 API，目前只查过 Flow 流水线部分
- [ ] 审计与合规：谁改了什么、AI 改了什么，留存要求？
