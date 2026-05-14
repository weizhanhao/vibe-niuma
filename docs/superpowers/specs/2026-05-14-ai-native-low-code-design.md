# AI 原生低代码平台 — MVP 设计文档

**日期：** 2026-05-14
**状态：** 已确认，待转入实现计划

---

## 1. 背景与目标

传统企业低代码平台的底层假设是「拖拽组件比写代码快」。在 AI 时代，瓶颈已不再是 UI 拼装，而是**业务意图的表达和落地**。

本项目的核心交互：业务员在自己正使用的 web 产品页面上**框选一块区域**、用自然语言说出想要的业务结果；系统自动定位到对应源码、由 AI coding agent 在真实代码库里完成修改、构建并起一个隔离的预览环境；业务员看预览、验收、确认合并。程序员只维护「可编辑表面」与系统本身，不再逐条处理业务员的小需求。

**长期愿景**（非本 MVP 范围）：多栈适配、私有化部署、面向 B 端多租户。

**本 MVP 的目标**：用一个我们自己搭建的 demo 应用作为「被改的目标产品」，把整条闭环链路完整跑通，验证可行性。

---

## 2. 已锁定的关键决策

| 维度 | 决策 |
|---|---|
| 范围 | 完整闭环 MVP（捕获 → 澄清 → 改代码 → 预览 → 合并） |
| 被改的目标产品 | 自建 demo 应用：React + Vite + React Router 前端 + Python + FastAPI 后端 |
| 选区域机制 | 页面级 + 框选；捕获 URL + 高亮框截图 + 坐标；后端用 URL→路由定位入口源文件，LLM 自己在仓库展开 |
| dev runner | claude-code（可插拔 adapter，模型可经兼容代理走 DeepSeek/国产模型）；同时落 opencode 实现 |
| 预览/合并 | 每个变更请求一个 git 分支 + 一个独立容器；业务员看预览 → 自审确认 → 合并分支 |
| 界面/角色 | 全部在浏览器扩展内，单角色自审 |
| 运行环境 | 单台阿里云 ECS，所有组件（demo 仓库、Orchestrator、dev runner、预览容器、MySQL）都跑在上面 |
| 澄清交互 | 自适应：轻改动走文字问答，重改动生成 HTML 方案让业务员选；做成可插拔 skill；只问业务、不碰技术 |
| 数据库 | MySQL（Orchestrator 的变更请求仓储 + demo 后端数据，各连各的 database） |
| 安全 | 本 MVP 明确不考虑 |

### 架构方案：单体 + 四个 Adapter 接口

主体是 ECS 上一个 FastAPI 单体服务，异步长任务用进程内 background task（不引入独立任务队列）。系统中所有「栈相关 / 工具相关」的代码都收敛到四个 adapter 接口背后，每个接口在 MVP 阶段落一个具体实现。换栈、换 dev runner、换预览方式、换交互策略，只动对应 adapter，Orchestrator 主体与状态机不变。

---

## 3. 整体架构

### 3.1 系统组成（5 块）

```
┌─────────────────────┐         ┌──────────────────── 阿里云 ECS ────────────────────┐
│  浏览器扩展          │         │                                                    │
│  (用户本地 Chrome)   │  HTTPS  │   ┌──────────────────────────────────────────┐     │
│  · 框选区域          │ ──────► │   │  Orchestrator (FastAPI 单体服务)         │     │
│  · 输入需求          │  REST   │   │   REST API + SSE + 状态机 + 任务编排器    │     │
│  · 澄清问答 / 选方案 │ ◄────── │   │   ┌────────────┐┌────────────┐┌────────┐ │     │
│  · 看状态 (SSE)      │  SSE    │   │   │Interaction ││StackAdapter││Preview │ │     │
│  · 开预览            │         │   │   │Skill       ││            ││Adapter │ │     │
│  · 确认合并 / 丢弃   │         │   │   └────────────┘└────────────┘└────────┘ │     │
└─────────────────────┘         │   │   ┌────────────────────────┐             │     │
                                │   │   │ DevRunnerAdapter       │             │     │
                                │   │   └────────────────────────┘             │     │
                                │   └──────────────────────────────────────────┘     │
                                │      │            │              │          │      │
                                │      ▼            ▼              ▼          ▼      │
                                │  ┌────────┐ ┌──────────┐ ┌────────────┐ ┌────────┐ │
                                │  │demo 仓库│ │分支cr/<id>│ │预览容器     │ │ MySQL  │ │
                                │  │(git,main)│ │上改代码  │ │(每请求一个) │ │        │ │
                                │  └────────┘ └──────────┘ └────────────┘ └────────┘ │
                                └────────────────────────────────────────────────────┘
```

1. **浏览器扩展** — 用户本地 Chrome。负责捕获（框选 + 截图 + URL）、澄清问答 / 选方案、状态展示、开预览、确认合并 / 丢弃。薄客户端，不含业务逻辑。
2. **Orchestrator** — ECS 上一个 FastAPI 单体服务。系统的大脑。对外 REST + SSE。异步长任务用进程内 background task。
3. **四个 Adapter** — 在 Orchestrator 进程内，干净接口 + 各一个 MVP 实现。
4. **demo 仓库** — ECS 上一个 git 仓库，`main` 分支是 demo 产品的当前状态。每个变更请求从 `main` 切一个 `cr/<id>` 分支。
5. **预览容器 + MySQL** — 每个变更请求一个容器；MySQL 单实例供 Orchestrator 与 demo 后端使用。

### 3.2 闭环数据流（一个变更请求的一生）

```
1. 业务员在 demo 页面 → 开扩展 → 框选 → 输入业务需求
2. 扩展 POST /change-requests  { url, screenshot(高亮框), boxCoords, viewport, requestText }
3. Orchestrator: 建请求记录 → git 切分支 cr/<id> → 返回 id → 扩展订阅 SSE
4. Orchestrator 后台任务:
   a. clarifying  : InteractionSkill.clarify() 与业务员一来一回，产出 RequestBrief
   b. located     : StackAdapter.locate(url) → URL 映射到路由入口源文件
   c. coding      : DevRunnerAdapter.run(分支, DevContext) → 改代码并 commit
   d. building    : StackAdapter.build(分支) → 构建；PreviewAdapter.serve(分支) → 起容器
   e. preview-ready: 拿到预览 URL
   (全程通过 SSE 推送状态变迁)
5. 扩展显示「预览就绪」+ URL → 业务员打开、刷新、验收
6. 业务员点「确认合并」→ Orchestrator rebase 到最新 main → 合并 cr/<id> → 拆容器
   或点「丢弃」        → 删分支 + 拆容器
```

### 3.3 关键设计点

- **状态机驱动**：每个变更请求是一个有限状态机；扩展只是这个状态机的观察者。
- **分支 + 容器即隔离单位**：并发的多个请求互不干扰，各自在独立分支 + 独立容器。
- **Adapter 是唯一的「栈/工具相关」代码**：换栈、换 dev runner、换预览方式、换交互策略，只动对应 adapter。

---

## 4. 组件设计

### 4.1 Orchestrator 核心（与栈/工具无关）

| 模块 | 职责 |
|---|---|
| REST API | `POST /change-requests`、`GET /change-requests/{id}`、`POST /change-requests/{id}/answer`、`POST /change-requests/{id}/merge`、`POST /change-requests/{id}/discard`、`POST /change-requests/{id}/retry` |
| SSE 端点 | `GET /change-requests/{id}/events` — 推送状态机变迁；事件类型含 `status`、`question`、`variants` |
| 状态机 | `created → clarifying → located → coding → building → preview-ready → merged`；任一步 `→ failed(phase, reason, log)`；`preview-ready` 闲置超时 `→ expired` |
| 任务编排器 | 进程内 background task，串起 clarify → locate → run → build/serve，每步后写状态 + 推 SSE |
| 变更请求仓储 | 请求记录持久化（id、url、状态、分支名、预览 URL、错误信息、时间戳）。MySQL |
| Git 管理器 | 切分支、提交、rebase、合并到 main、删分支 |
| 容器配额管理 | 进程内信号量控制并发容器数上限；闲置回收器定时拆超时未操作的容器 |

### 4.2 四个 Adapter 接口

```python
# —— 交互层：只问业务，不碰技术 ——
class InteractionChannel(Protocol):
    async def ask(self, question: str, options: list[str] | None) -> str:
        """文字问题，可带文字选项；经 SSE 推给扩展，等扩展回答"""
    async def present_variants(self, variants: list[HtmlMockup]) -> VariantSelection:
        """展示 HTML 方案，业务员选一套或全否"""

class InteractionSkill(Protocol):
    async def clarify(self, raw: RawRequest, channel: InteractionChannel) -> RequestBrief:
        """驱动澄清对话，产出精炼后的业务级需求 brief"""

# —— 栈层：URL→源码、构建 ——
class StackAdapter(Protocol):
    def locate(self, url: str) -> LocateResult:
        """URL → 路由入口源文件"""
    def context_pack(self, locate_result, screenshot, box_coords, brief) -> DevContext:
        """组装给 dev runner 的上下文包"""
    def build(self, repo_path: str, branch: str) -> BuildResult:
        """构建分支代码（最终关卡）"""

# —— 开发层：业务 brief → 真实代码改动 ——
class DevRunnerAdapter(Protocol):
    def run(self, repo_path: str, branch: str, ctx: DevContext) -> RunResult:
        """在分支上改代码并 commit；内部自行跑构建并修到绿"""

# —— 预览层：分支 → 隔离预览环境 ——
class PreviewAdapter(Protocol):
    def serve(self, repo_path: str, branch: str) -> PreviewInstance:
        """构建并起预览环境，返回预览 URL"""
    def teardown(self, instance: PreviewInstance) -> None:
        """拆掉预览环境"""
```

### 4.3 MVP 实现清单

| Adapter | MVP 实现 | 说明 |
|---|---|---|
| `InteractionSkill` | `BrainstormingSkill` | 自适应：轻改动走文字问答（一次一问、最多 3 问、可跳过），重改动生成 2-3 套轻量 HTML 方案让业务员选。只产出业务问题，prompt 层强制不涉及技术。 |
| `StackAdapter` | `ReactViteStackAdapter` | 解析 React Router 配置，URL path 匹配到路由组件文件；含动态路由（`/orders/:id`）处理。 |
| `DevRunnerAdapter` | `ClaudeCodeDevRunner` + `OpenCodeDevRunner` | 两个实现。「dev runner 工具」与「模型」是两根独立的轴：模型是配置项。claude-code 经 `ANTHROPIC_BASE_URL` 指向兼容代理即可使用 DeepSeek / 国产模型；opencode 原生多 provider。用哪个 runner、配哪个模型，全在配置文件里决定。 |
| `PreviewAdapter` | `DockerPreviewAdapter` | 每个变更请求构建镜像 + 起容器 + 端口映射，得到预览 URL。 |

### 4.4 职责分层原则

- **业务层（`InteractionSkill`）** 决定 **WHAT** — 业务结果应该是什么样。业务员永远不会被问到任何技术问题。
- **技术层（`DevRunnerAdapter`）** 决定 **HOW** — 用什么组件、改哪个文件、用何种实现方式。
- 因此 `RequestBrief` 是一份**业务级需求**，可能带一个「业务员选中的 HTML 方案」作为意图锚点；DevRunner 把业务 brief 翻译成真实代码库里的技术实现，选中的 HTML 方案仅作目标效果参考。

### 4.5 国内网络方案

阿里云 ECS 在国内，claude-code 依赖的 Anthropic API 直连不了。解决方式：在 ECS 上架一个「Anthropic API 兼容代理」（claude-code-router / LiteLLM / one-api 这类），claude-code 通过 `ANTHROPIC_BASE_URL` 指向它，代理把请求转发到 DeepSeek 或通义千问等国内可访问模型。`OpenCodeDevRunner` 则直接利用 opencode 的原生多 provider 能力。两个实现都做，配置决定运行时用哪个。

### 4.6 自适应澄清的边界约束

`BrainstormingSkill` 重路径生成的是「轻量、独立、一次性的 HTML mockup」，**不是真实代码库的构建产物**，仅用于传达意图。这条线划清楚，重路径才不会退化成「澄清阶段跑一个迷你 DevRunner」，MVP 才扛得住。业务员选中的方向之后由 DevRunner 在真实代码库里正经实现。

---

## 5. 错误处理与边界情况

### 5.1 状态机补全

`failed` 带 **phase（哪一步崩的）+ reason（为什么）+ log（证据）**。完整状态机：

```
created → clarifying → located → coding → building → preview-ready ──(业务员点合并)──> merged
   │          │           │         │         │            │
   └──────────┴───────────┴─────────┴─────────┴────────────┴──→ failed(phase, reason, log)
                                                            └──→ expired (闲置超时被回收)
   created ──(配额满)──→ 停在 created 排队，槽位空出再启动
```

`merged` 不是流水线自动到达的状态，而是由业务员在 `preview-ready` 主动点「确认合并」触发的转换；该转换内部先 rebase 再 merge，失败则 `failed(merging, conflict)`。`merging` 是失败 phase 标签，不是一个独立的停留状态。

### 5.2 失败模式

| 失败点 | 检测 | 进入状态 | 业务员看到 | 恢复 |
|---|---|---|---|---|
| locate 失败（URL 不匹配任何路由） | StackAdapter 返回空 | `failed(located)` | 「没能定位到这个页面对应的代码」 | 重新发起 |
| DevRunner 报错（崩/超时/限流） | 子进程非 0 退出 / 超时 | `failed(coding)` | 「AI 改代码时出错了」+ 日志 | 重试 |
| DevRunner 没产出（跑完无改动） | git diff 为空 | `failed(coding, no-changes)` | 「AI 没做出改动，需求可能太模糊」 | 补充描述重发 |
| 构建失败（代码编译不过） | 构建非 0 退出 | `failed(building)` | 「改动构建不通过」+ 构建日志 | 重试 |
| 容器起不来 | 容器未进入 healthy | `failed(building, container)` | 「预览环境启动失败」 | 重试 |
| 并发超配额 | 信号量无空闲槽 | 停在 `created`（排队） | 「排队中（前面还有 N 个）」 | 自动，槽位空出就启动 |
| 业务员晾着不操作 | 预览实例 last-activity 超时 | `expired` | 「预览已过期」 | 重新发起 |
| 合并冲突 | git rebase/merge 冲突 | `failed(merging, conflict)` | 「代码已变化，无法自动合并」 | 丢弃后重做 |
| SSE 断连 | 扩展侧检测 | 状态不变 | 自动重连 + 拉 GET | 见 5.3 |
| Orchestrator 重启 | 启动时扫描非终态请求 | 非终态 → `failed(interrupted)` | 「服务重启，请求中断」 | 重试 |

### 5.3 跨切面策略

1. **构建验证下沉到 DevRunner**：`DevRunnerAdapter.run` 内部就让 dev runner 自己跑构建并修到绿；流水线里的 `StackAdapter.build` 是最终关卡。
2. **SSE 只是优化，GET 才是真相**：`GET /change-requests/{id}` 返回完整当前状态，状态持久化在 MySQL。SSE 断了，扩展重连 + 拉一次 GET 即可。
3. **资源清理是 best-effort 且幂等**：进入 `failed` / `expired` / `discard` 触发清理（拆容器、释放端口）。**分支保留**，便于事后翻看 AI 改了什么；只有业务员主动「丢弃」才删分支。
4. **配额用进程内信号量**：容器并发上限是配置值；超了的请求停在 `created` 排队，不拒绝。`merged`/`failed`/`expired`/`discard` 释放槽位。
5. **闲置回收器**：后台 reaper 定时扫 `preview-ready` 且 last-activity 超过 TTL 的请求 → 标 `expired` + 拆容器。

### 5.4 重试语义

`failed` 的请求可「重试」：从当前 main 切一个新分支重跑整条流水线（不复用旧分支，因为 main 可能已变）。原失败分支保留待查。每次重试都是干净的新分支。

### 5.5 合并冲突的 MVP 取舍

MVP 不做自动 rebase 化解冲突。合并前先尝试把分支 rebase 到最新 main，rebase 干净就合并，冲突就 `failed(merging, conflict)`、让业务员丢弃重做。自动化解冲突留作后期增强。

---

## 6. Demo 应用定义

被改的目标产品 —— 一个**订单管理 mini 应用**（贴合「业务员用的内部工具」定位），同时是 MVP 的测试夹具。

**前端**（React + Vite + React Router）4 条路由：

| 路由 | 页面 | 用来测什么 |
|---|---|---|
| `/` | 看板首页（统计卡片） | 「重」改动面（重排卡片布局 → 触发 HTML 方案选择） |
| `/orders` | 订单列表（表格） | 表格类组件的区域框选 |
| `/orders/:id` | 订单详情 | 动态路由 → URL→路由映射的难 case |
| `/settings` | 设置表单 | 「轻」改动面（改按钮颜色/文案 → 走文字澄清） |

**后端**（Python + FastAPI）：几个 endpoint 供 mock 数据（MySQL）。让闭环能覆盖「改动只涉及前端」和「改动要动后端接口」两种。

**容器化**：demo 仓库自带 `Dockerfile`（前端、后端各一），供 `DockerPreviewAdapter` 使用。

demo 的路由表、组件结构是 `ReactViteStackAdapter` 的契约测试基准。

---

## 7. 测试策略

核心矛盾：DevRunner 和 InteractionSkill 是 LLM 驱动的、非确定性。分层应对：

| 层 | 方法 | 覆盖 |
|---|---|---|
| 确定性测试（主体，进 CI） | Mock 掉 `DevRunnerAdapter` 和 `InteractionSkill` | 状态机变迁、Git 操作、容器生命周期、REST API、SSE、配额信号量、闲置回收器、所有失败路径 |
| 契约测试 | 每个 adapter 一套契约测试 | `ReactViteStackAdapter.locate` 对 demo 各 URL 返回正确路由文件；`DockerPreviewAdapter` 真起容器；`BrainstormingSkill` 的轻/重判定逻辑（mock LLM） |
| 真实 E2E 冒烟（手动/夜间，不进快速 CI） | 一次脚本化真实运行 | 对 demo 跑一个已知简单改动，断言预览起得来、diff 非空 |
| 扩展单测 | 前端单测 | 框选坐标计算、截图捕获、URL 提取、问答/方案 UI 渲染 |

目标覆盖率 80%，靠确定性测试 + 契约测试达成；E2E 冒烟是信心补充，不算进覆盖率。

---

## 8. MVP 范围边界

### 明确做（IN）

- 一个 demo 应用 + 四个 adapter 各一实现（DevRunner 两个）
- 完整闭环：框选捕获 → 自适应澄清 → 改代码 → Docker 预览 → 自审确认 → 合并
- 单用户、单角色、自审
- 全跑在一台 ECS
- SSE 状态流、失败状态、重试、配额、闲置回收

### 明确不做（OUT，防范围蔓延）

- 安全 / 认证
- 多角色 / 审核闸门（程序员 reviewer）
- 多栈（只 React+Vite+FastAPI；其它栈是未来的 adapter）
- 容器加固 / 生产部署 / 私有化部署
- B 端 / 多租户
- DOM 元素级精度（只做页面级 + 框选）
- 自动化解合并冲突
- 扩展之外的持久化 dashboard / 历史界面

---

## 9. 最该先验证的 3 个风险假设

1. **URL→路由源文件映射的可靠性** — `StackAdapter.locate` 是整条链的地基，动态路由 `/orders/:id` 尤其要验。
2. **dev runner 能否凭「业务级 brief + 截图」产出够用的改动** — 决定闭环体感。
3. **Docker 预览的启动延迟是否可忍** — 选了容器，`building` 阶段会偏长，要实测。

---

## 10. 未来阶段（非本 MVP）

- 多栈适配（Vue、SSR 模板、其它后端语言）—— 新增 adapter 实现
- 多角色与审核闸门（业务员提需求、程序员审核把关）
- 容器加固、私有化部署、SSH 打通客户基础设施
- B 端多租户
- DOM 元素级精度（构建时注入 `data-source`）
- 自动化解合并冲突
- 「可编辑表面」机制：程序员定义 slot / 配置 schema / design token，约束 AI 与业务员的可操作范围
