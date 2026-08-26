# 三方专家审查与修复记录

2026-08-24，三位独立专家分别从**需求符合度**、**代码正确性**、**交付完成度**
三个视角审查 v2。本文记录他们发现了什么、修了什么、还剩什么。

## 最重的一条：没有装配根

三位专家**独立**指向同一处：`handlers.configure()` 在生产代码里零调用，
`_caps` 永远是空 `Capabilities()`。

后果不是"少个功能"，是**整套系统从没被组装过**：
`WorktreeDockerProvider` / `CliSession` / `OcrReviewAdapter` / `FindingFilter` /
`SelfHostedDeploy` 五个真实现，在 `src/` 里没有一处生产代码 new 过它们 ——
它们只活在 `scripts/demo.py` 和 `tests/` 里。

用户视角：提一条需求 → 卡片从"分诊"依次走到"交付" → 状态显示 done →
**仓库里一行代码都没变**。而 110 个测试全绿，因为它们测的正是这条空转路径。

**已修**：新增 `bootstrap.py`（按 project 装配，不是全局单例），
`worker_main.main()` 和 API `lifespan` 都调 `install()`。

## 三处「假成功」—— 比崩溃危险

| 位置 | 之前 | 现在 |
|---|---|---|
| `verify()` | 返回「已跑 lint, test, build」，**一条命令都没执行** | 用 `workspace.exec()` 真跑，按仓解析命令，失败标 Run |
| `decompose()` | 有 agent 时只 `logger.info(prompt)` 就返回「已提交拆解」 | 真调 agent，读回 `.scratch/<req>/issues/`，落 Task + TaskTouch |
| `ai_review()` | 读 `commit_shas["_workspace"]`，而生产从不写它 → 恒 0 发现 | 从 `Workspace` 表 rehydrate 工位 |

这三处的共同点：**UI 上显示"已完成"，人会据此点批准**。
静默假成功比崩溃难查得多。

## 环节完成度

之前 12 个环节：真做事 1、部分 2、空转 6、假做事 3。

现在 `DISPATCH` 覆盖 `decompose / implement / verify / ai_review / preview /
merge / integrate / deploy_test / release`，且**环节失败或缺能力会阻断流水线**
（之前无条件推进到下一环节）。

## 逐条修复

### CRITICAL

**C1 · `fetch --prune '+refs/*:refs/*'` 删掉活跃工位的分支** ——
第二个 Run 一 acquire 就把第一个 Run 的 `cr/<id>` 分支 prune 掉，
agent 已提交的代码变成不可达对象直接蒸发。git 不保护活跃 worktree 的分支。
**已修**：只取到 `refs/remotes/origin/*`，clone 后显式改掉 mirror refspec。
回归测试 `test_fetch_does_not_prune_active_run_branches`。

### HIGH

| # | 问题 | 修法 |
|---|---|---|
| H1 | `advance_requirement` 忽略 `StageOutcome.ok`，失败的需求照样走到审核和部署 | 失败 → `state=failed` 停下；缺能力 → `state=blocked` 停下 |
| H2 | 「打回改」是死路：没入队 job，且幂等键已被占死 | 带返工轮次重开幂等键 + 清 step 缓存 |
| H3 | `release` 闸门无法批准（硬编码 `stage != "review"`） | 改判「当前 stage 是不是人工闸门」 |
| H4 | `ai_review` 恒 0 发现 | 见上表 |
| H5 | 工位开了从不 release，100% 泄漏；端口租约是死代码 | 失败即释放 + 落 `Workspace` 表 + reaper 兜底；真租端口 |
| H6 | handler 抛异常时半提交业务写 + 已 dispatch 事件，然后重试 | 失败整体回滚，重试在独立事务里安排 |
| H7 | rebase 根本没启动时报告「已解决」 | 新增 `aborted_reason`，`resolved` 要两者都满足 |
| H8 | AI 解完的文件从不 `git add`，且失败不 abort → 仓库卡死 | 补 add + 校验残留标记 + 失败必 abort |
| H9 | 事件回放边界**永久**丢事件（id 分配序 ≠ 提交序） | 阈值游标 → 已见 id 集合 |
| H10 | `session.rollback()` 炸掉调用方整个事务 | 改用 `begin_nested()`（SAVEPOINT） |

### MEDIUM / LOW

`attempts` 自增独立事务（毒丸 job 无限重试）、worktree fallback 挂到陈旧分支、
空 repos 列表抛 `StopIteration`、前端切空间响应竞态、`touchCollisions` 自冲突、
前端不消费 SSE、部署 readline 无超时、过滤层 fail-open 不完整、
接缝守卫的 `from X import Y` 漏洞、SSE 安静流不回收、ocr 报错丢 stderr、
非审核态加载错误不可见 —— **全部已修，各有回归测试**。

## 补上的缺失能力

- **管理端 API**（之前第一个真实用户没有入口）：引导 / 建空间 / 加仓 / 加成员 / 签 token / 配流水线 / 配部署
- **前端认证**：登录页 + bearer token + localStorage；`X-User` 收紧到只在 `VP_DEV_AUTH=1` 生效
- **`hosts/github.py`**：clone / fetch / **push** / open_change / comment / verify_webhook
- **reaper**：僵尸工位回收 + `jobs_archive` 冷热分表 + 过期端口租约
- **数据库迁移**：版本化迁移器 + CLI + 21 张表的初始迁移
- **优雅关闭**：SIGTERM 等当前 job 跑完（之前 docker stop 直接杀，工位泄漏 900 秒）
- **部署**：Dockerfile 多阶段构建 skill dist（之前 COPY 一个 gitignore 掉的目录，必然失败）、前端镜像 + nginx、compose 加 migrate 步骤和 healthcheck

## MySQL 实测发现的三个 bug

专家指出「MySQL 路径零验证」。接上本机 MySQL 后跑通迁移 + 全套测试 + 专项验证，
**发现三个只在 MySQL 上出现的问题**：

1. **迁移器静默丢语句** —— 按 `;` 切分时文件开头注释和第一条 DDL 粘在一块，
   整块被当注释丢掉。69 条变 68 条，`orgs` 表根本没建，后面所有外键失败。

2. **无精度 `DATETIME` 把小数秒进位** —— `11:59:21.600` 存进去变成 `11:59:22`。
   于是 `enqueue` 写的 `next_run_at` 可能比真实时间晚将近一秒，
   **约四成的 job 隐身最多 500ms**，直接打破「交互 lane 200ms 秒回」的承诺。
   全部 34 个时间列改成 `DATETIME(6)`。

3. **测试自己没按真实约束写** —— reaper 测试塞了不存在的 `run_id`，
   sqlite 默认不检查外键所以过了，MySQL 上炸。

另外把 MySQL 版本从浮动的 `mysql:8` 锁到 `mysql:8.0`
（本机是 9.3，验的和部署的不是同一个东西）。

## 端到端接真实仓库发现的 bug

接 `weizhanhao/doBuyRight`（**私有仓**，158 个 py 文件，104 个测试）时发现：

接上真实私有仓 + 真容器后，一共挖出 **5 个新 bug**，全部是模拟环境碰不到的。

### E1 · `_repo_specs()` 从不解析 `pat_ref` —— 私有仓永远 clone 不下来

`ProjectRepo.pat_ref` 字段建了、管理端能配、文档写了，但转成 `RepoSpec` 时压根没读它。
报的是 `Authentication failed`，看不出是平台根本没传凭证。

demo 用本地 `file://` 路径、公开仓不需要凭证 —— 所以一直没暴露。
**已修**，且凭证解析失败时明说是哪个 `pat_ref` 的问题。

### E2 · 容器内 git 完全不能用（最严重）

git worktree 的 `.git` 文件里写的是 bare mirror 的**宿主绝对路径**，
mirror 那边的 `gitdir` 也反指回 worktree 的绝对路径。
之前只把 `ws_root` 挂到 `/w`，容器里那两个路径都不存在：

```
fatal: not a git repository: /private/tmp/.../mirrors/doBuyRight.git/worktrees/cr1
```

后果：**agent 没法 commit、`_collect_commits` 拿不到 sha、冲突阶梯全废**。
测试里走 `FakeDocker` 替身，所以从没暴露过。

**已修**：挂整个 project 目录到**同名路径**（这也正是 compose 里必须用
bind mount 而不是 named volume 的原因）。修复后实测：

```
✓ 当前分支: cr/ct-1
✓ git commit
✓ 相对 main 提交数: 1        ← _collect_commits 依赖的正是这个
```

### E3 · 我自己的预烘焙代码里留着 `|| true`

`RUN ... || true` 让 `pip install` 失败也算构建成功 —— 产出一个
「看起来烘焙了实际什么都没装」的镜像（`pip list` 只有 pip/setuptools/wheel），
到 verify 环节才报 "No module named pytest"。

**这正是我在 `Dockerfile.workspace` 里批评过的反模式，却留在了自己代码里。**
已去掉，失败改成 `logger.error` 吼出来并如实回落 base 镜像。

修复后实测：`import pytest, flask, pandas` 全部就绪，
容器内跑 `doBuyRight` 真实测试 `2 passed, 3 errors`
（那 3 个 error 是该仓自己需要数据库，平台如实报出来了，没有假装通过）。

### E4 · 有 `pytest.ini` 却没声明 pytest 的项目

`doBuyRight` 有 `pytest.ini` 和 104 个测试文件，`requirements.txt` 里却没有 pytest。
verify 会报 "No module named pytest"，看起来像平台的问题。
预烘焙现在检测到 `pytest.ini` / `tox.ini` 就补装 pytest。

### E5 · clone 超时 1800 秒且无重试

实测遇到两次网络故障：一次 `Empty reply from server`，
一次 **CPU 只用了 0.02 秒却挂了 13 分钟的 `git clone`**。
超时设 1800 秒意味着一个卡死的 clone 能占住 worker 半小时。

**已修**：超时收紧到 600 秒，按错误特征区分瞬时/永久故障
（认证失败不重试，网络类退避重试 3 次），clone 失败清掉半成品目录。

### E6 · 会话 id 自己编 → `Session not found`

`CliSession.create()` 自己发了个 `ses_<uuid>` 就往 `--session` 里传。
opencode 直接打回 —— `--session` 的语义是「**续接**已存在的会话」
（`opencode run --help` 原文 "session id to continue"），
新会话只能由不带该参数的 run 隐式创建。

**这意味着「会话是一等公民」这个头号卖点，在 CLI 路径上从第一步就是坏的。**

**已修**：首次不带 `--session`、从 `--format json` 事件流捕获真实 id 并落库；
续改带 id；分叉 `--session <父> --fork`。这一层之前**零测试**。

### E7 · `--model` 缺 provider 前缀

平台传 `deepseek-v4-pro`，opencode 要 `provider/model`：
`Model not found: deepseek-v4-pro/.`

### E8 · opencode 失败时退出码是 0

上面那个 `Model not found`，**退出码是 0**，错误只藏在
`{"type":"error"}` 事件里。适配器只看退出码 → **把失败当成功**，
下游拿到一个「跑完了但什么都没做」的结果。

**已修**：必须扫事件流里的 error。

### E9 · `_code` 被批量重构误删，调用还在

单测全过，端到端跑到一半才炸 `AttributeError`。
**已修**，并加了 AST 静态守卫：扫 `StageRunner` 里所有 `self.X(...)` 调用，
确认方法都存在。

### E10 · ticket 解析器太脆

agent 产出了一份质量很高的拆解（读懂了 `_avg_holding_days` 的 FIFO 配对逻辑、
跳过 `excluded_from_stats` 记录、行号都对），但写成自由格式而非 to-tickets 模板，
解析器直接判「未产出 ticket」，整份丢掉。

**解析器太脆比太松更糟** —— 后者至少还能人工修。
**已修**：优先认严格模板，认不出就从标题和行内代码路径宽松兜底。
agent 这份真实产出存成了测试夹具 `tests/fixtures/real_agent_ticket.md`。

### E11 · HTTP/2 framing 错误不在瞬时列表

`Error in the HTTP2 framing layer` 是 git + GitHub 的已知问题。
**已修**：加进瞬时错误列表，并给 mirror 配 `http.version=HTTP/1.1` 缓解。
顺带加了 **fetch 节流** —— 之前每次 acquire 都打网络，
一条需求 N 个任务就是 N 次往返，撞抖动的概率成倍放大。

### E12 · ticket 找错地方

opencode 的项目根检测会落到 git 仓那一层，agent 把 `.scratch/...`
写进仓库目录，而平台在工位根找。
**已修**：工位根和每个仓库目录都找，去重。

### E13 · 失败即删工位，证据一起没了

失败只剩一句「agent 跑完了但没有产生任何 commit」，**根本没法查它干嘛去了**。

**已修**：失败的工位保留待查（由 reaper 按 TTL 回收），
agent 的原话和工作区 `git status` 写进 `run.fail_log`。

**这一条是发现 E14 和 E15 的前提** —— 没有证据就永远查不出根因。

### E14 · agent 跑在平台自己的仓库里（最严重）

E13 修完后，诊断日志立刻暴露：

```
read platform/tests/fixtures/real_agent_ticket.md   ← 读的是平台的测试夹具
bash Search for doBuyRight repo in home             ← 满硬盘找目标仓
```

工位根目录不是 git 仓（`doBuyRight/` 是它的子目录），opencode 的项目根检测
往上走找不到，**回落到了平台自己的代码库**。

后果不只是干不成活 —— **它有权修改那个仓**。

**已修**：单仓时把 agent 的 cwd 设成仓库目录，并显式传 `--dir` 钉死。

### E15 · opencode 的会话绑定目录，跨目录 fork 不成立

```
Failed to init file picker: Invalid path .../workspaces/plan-.../doBuyRight
```

拆解会话在 plan 工位、实现任务在各自的 run 工位。fork 继承父会话的目录，
而那个工位已经被回收了。

**已修**：`AgentSession` 加 `cwd` 列，**只在同目录时才 fork**；
跨目录就新建会话，把拆解结论写进 prompt 而不是靠会话继承。

---

## 这一轮的方法论收获

**① 可观测性是正确性的前提。**
E13（失败即删工位）修完才发现 E14 和 E15 —— 没有证据就永远查不出根因。
三条连起来说明：留痕不是锦上添花。

**② 测试替身跟真实现契约不一致时，测试保护的是幻觉。**
`_StubAgent.create()` 不透传 `parent`，于是「同目录才 fork」的测试
一开始测的是替身的行为而不是真实契约。

**③ 静默失败比崩溃难查得多。** 这一批 15 个里，
E3（`|| true`）、E8（退出码 0）、E10（解析器丢产出）、E13（删证据）
四条都属于「看起来成功了」。

---

## 拆解环节的实证

第 9 次端到端，`decompose` 首次完整跑通 —— 这是 §8 设计第一次成立：

```
拆出 2 个任务 + 2 条契约

T1 后端  touches: backend/engine/strategy_metrics.py
                  tests/test_strategy_metrics.py
                  tests/test_rules_metrics_api.py
T2 前端  touches: frontend/rules.html

契约：Provides `win_rate` (float 0.0~1.0, None when no closed trades)
      Depends on 同一字段
```

AI 自动拆成两个任务、各自声明 `touches`、跨层靠接口契约解耦所以能并行。
**不是编的示例，是它读 `doBuyRight` 真实代码后产出的。**

## 现在的状态## 现在的状态

| | |
|---|---|
| 后端测试 | **157 passed**，sqlite 与 MySQL 8 **双跑全绿** |
| 前端测试 | 29 passed，TypeScript 干净 |
| 接缝守卫 | ✓ 5 层完好（且守卫自己有测试） |
| MySQL 专项验证 | 7/7（SKIP LOCKED 并发抢占、索引长度、BIGINT、LONGTEXT、编号原子性） |
| 迁移 | 在真 MySQL 上跑通且幂等 |
| CI | sqlite + MySQL 双跑 + 镜像构建 |

## 仍然没做的（如实列出）

1. **容器路径从未真实执行** —— 本机 docker daemon 没起来，
   `use_container=True` 那条路径（docker build / run / exec）测试里走 `FakeDocker` 替身。
   **compose 的 DinD 路径映射是按分析改的，没有实跑验证。**
2. **`opencode serve` 的 ServerSession 未实测** —— 只测了 `CliSession`（已用真 DashScope 跑通）。
3. **端到端「一条真实需求改真实代码并合并」未跑通** —— 需要真实仓库 PAT。
   各段分别验证过（工位隔离、冲突三档、真实 ocr 复核），但没串成一条。
4. **前端 SSE 只有单元测试** —— jsdom 里没有真 EventSource。
5. `wayfinder` / `to-spec` / `triage` 等 skill 的实际调用效果未验证 —— 它们装进了镜像，
   但 agent 真调起来是什么效果没测过。

---

# 第二轮：交互面审查（2026-08-25）

问题不在底座，在**人机接触面**。底座跑通了一条真实需求，
但用户坐在界面前**没有地方可以说话**，也**看不见出了什么事**。
逐个环节问「这一步用户要做什么、界面上有没有」，找出 7 个缺口。

## I1 澄清环节根本不存在 — 严重

`clarify` 在 `DISPATCH` 里没有条目，直接 `{"ok": True}` 空转。
需求写得再含糊也一路往下拆。而「对需求」是产品和 AI 之间**必须来回**的环节 ——
业务员表达不清楚，后面拆解和实现全是白干。

**改**：`stages.clarify()` 真调 agent 提问（≤3 个、≤3 轮），落 `Message` 表，
`park_for_signal()` 挂起等人回答；`handlers` 认 `result["awaiting"]` 挂起不推进；
新增 `GET/POST .../messages`；前端 `Conversation` 面板。
「✓ 够了直接干」随时可跳过追问 —— 不给出口的追问会把人困住。

**回归测试**：8 个（提问并挂起 / READY 放行 / 未答不重复问 / 用户跳过 /
三轮封顶 / prompt 带完整对话 / 无 agent 不假装问过 / 问题抽取）

## I2 中文问题被长度阈值吃掉 — 中

`_extract_questions` 要求 `len > 5`。「要记住吗？」正好 5 个字 → 丢弃。
一句完整的中文问题比同义英文短得多，按字符数一刀切必然误杀。
**改**：去掉问号后 ≥3 字且含真实文字（挡住「1?」这种编号残渣）。

## I3 闸门上留言会把审核叫醒，然后 409 — 中

`signal()` 把 job 从 `awaiting_signal` 拉回 `pending`，闸门 handler 拿不到
`review_decision` 又挂回去。这中间的窗口里，审核人点「通过」→
`submit_review` 找不到等信号的 job → **409「没有等待审核信号的任务」**。
**改**：闸门上的留言就是留言，记下即可，不碰 job。

## I4 需求挂了，界面上完全看不出来 — 严重

`req.state` 变成 `failed` / `blocked` 后，看板上的卡片**跟正常的一模一样**。
看着在跑，实际早停了；而且没有任何重开入口，只能去数据库改状态。

**改**：卡片上「卡住了」/「缺能力」标记；详情页横幅区分
「跑挂了」和「平台缺能力」（两者要做的事完全不同）；
新增 `POST .../retry` 从当前环节重开 —— **必须清 step 缓存**，
不清的话新 job 命中上一轮 done 的 step，「重试」变成什么都不做还报成功，
比不给按钮更糟。留言也顺带复活挂掉的需求（人来接手了）。

## I5 失败原因无处可查 — 严重

`Event` 表记了全过程，但只有 SSE 推「从现在起」的事件。
中途打开页面的人、需求挂了之后回来看的人，界面上一片空白。
`Run.fail_reason` 同样从未渲染。

**改**：`GET .../activity` 回放历史（真实 e2e 那条需求有 51 条记录）；
`TaskOut` 带上 `fail_reason` / `attempts`，贴在任务行上。

## I6 预览地址算出来了却没人渲染 — 中

`preview` 环节把地址写进事件 payload 就没了。
「业务员自己点开看效果」这个 v1 卖点在 v2 里等于不存在。
**改**：`GET .../previews`，**只在工位仍 `ready` 时给链接** ——
工位一回收端口就没人监听，给一个点开必然报错的链接比不给更糟。

## I7 只有 `review` 有决定按钮，`release` 没有 — 中

后端早已修成「按 `is_human_gate` 判断」，前端却还硬编码 `req.stage === "review"`。
需求走到上线闸门就永远卡着，界面上没有任何按钮。
**改**：按 `stages[current].human_gate` 渲染；上线的按钮文案单独写
（「放行上生产」，且提示不可撤销）。

## 顺带修掉的

- **E21 幽灵仓**：`run.commit_shas` 里塞过一条 `"_workspace": <路径>`，
  而 merge 拿 `commit_shas` 的键当仓名 —— 合并队列里冒出一个叫
  `_workspace` 的仓，永远合不掉也删不掉。工位路径本来就在 `Workspace` 表里。
- **流程节点中文化**：`Stage.label`（内置 12 个环节的中文名，
  自定义环节在 YAML 里写 `label:`）。`key` 保持 ASCII 作标识符 ——
  中文化的是显示层，幂等键、事件流、API 路径一个字都没动。
- **空间管理界面**：建空间 / 绑仓 / 加成员 / 签令牌之前只有 API 没有界面，
  第一个真实用户没有入口。令牌明文只显示一次（库里只存 sha256）。

## 第二轮验证

| | |
|---|---|
| 后端测试 | **234 passed** |
| 前端测试 | **46 passed**，TypeScript 干净，生产构建通过 |
| 接缝守卫 | ✓ 5 层完好 |
| 线上实例 | `messages` / `activity` / `previews` / `retry` / `admin` 全部 200 |

## 交互面仍然缺的

1. **看不到 AI 到底改了什么代码** —— 没有 diff 视图。审核人只能看
   findings 和任务标题，判断依据不足。这是下一个该补的洞。
2. **合并冲突退回后没有专门的处理页** —— 三档梯子失败只在流程记录里
   显示 `blocked`，没有「这里冲突了，你来定」的界面。
3. **@ 某人 / 通知** —— 需求停在你身上时没有任何提醒，只能自己刷页面。
4. **前端 SSE 仍只有单元测试**（jsdom 无真 EventSource），
   实时刷新靠轮询兜底。

---

# 第三轮：空间≠仓库，以及「需求要先谈出来」（2026-08-25）

## J1 一个空间不等于一个 git 仓 — 严重

数据模型一直支持多仓（`ProjectRepo` 一对多），**但分支模型是错的**。

`Project.target_branch`（集成分支）是**项目级单列**，却被当成每个仓都有的
一条分支用；而且 `stages.py` 里 `"vibe/dev"` **硬编码了 8 处**，
配置从来没被读过 —— 改了 `target_branch` 没有任何效果，也不报错，
只是默默在另一条分支上干活。

连带 `worktree_docker.py` 的
`base = base_branch or spec.default_branch` 里，**`spec.default_branch` 是死代码**：
调用方永远传集成分支名，所以每个仓自己的主干配置一次都没生效。

实际后果：探测不到 `origin/vibe/dev` 就把字面量当 ref，
`git worktree add ... vibe/dev` 报 unknown revision → **整个工位创建失败**。
单仓空间你手动建一次分支就绕过去了；多仓空间里只要有一个仓没建过
这条分支（新接进来的仓必然没有），这个仓就永远进不来。
老仓 `master`、新仓 `main` 混着更是直接躺平。

**改**：确立两级分支模型 ——
- 每个仓有自己的**主干**（`ProjectRepo.default_branch`，`main`/`master`/…）
- 空间有一条**集成分支**名（`Project.target_branch`），
  它在每个仓里是**各自的一条分支**；仓里还没有就从这个仓自己的主干起步

`_resolve_base()` 按 集成分支(远端→本地) → 仓主干(远端→本地) 逐级找，
都找不到时报的是「找不到任何可用的起点分支，试过 [...]」，
不是 git 那句 unknown revision。8 处硬编码全部改成读 `target_branch(project_id)`，
`_baseline_ok` / `_reverify` / `_collect_commits` / `_push` 的 base 由调用方显式传
（不从 ws 上猜 —— 猜会让替身与真实现脱节）。

**回归测试**：4 个（仓没有集成分支时落到自己主干 / 一个 main 一个 master 同时起 /
仓有集成分支时必须用它不能退回主干 / 两条都没有时报得能看懂）+ 3 个配置生效测试。

## J2 多仓时 agent 拿不到 git 上下文 — 中（已测量，部分缓解）

实测 opencode 1.17 对 `--dir` 的处理：

| `--dir` 指向 | 解析出的 project | vcs |
|---|---|---|
| 多仓工位根（非 git 目录） | **`global`**（内置兜底桶） | 无 |
| 单个仓目录 | 真实 project id | `git` |

好消息：它**不会**往上爬进外层仓库 —— `--dir` 钉住了，**E14 不会重演**。
坏消息：多仓时澄清/拆解/立需求的会话落进 `global`，没有任何 VCS 上下文。

而代码注释写着「prompt 里说明各仓是子目录」——**prompt 里根本没写这句**。
**改**：`repo_map()` 把每个仓的绝对路径 + 「工位根不是 git 仓」明确写进 prompt。
单仓时不输出（cwd 就是仓目录，opencode 上下文完整，别塞废话）。

**仍未解决**：`global` 项目本身。彻底修要么让工位根成为 git 仓
（有让 opencode 忽略嵌套仓的风险），要么改成挑一个主仓当 cwd
（有 agent 读不到兄弟仓的风险）。两条都需要真实模型调用才能验证，
本机 dashscope provider 没配上，**没验证过的改动我没有合进去**。

## J3 提需求是个表单，不是一段对话 — 严重

`POST /requirements` 填完标题正文**直接进 triage 往下跑**。
可是业务员坐下来时脑子里往往只有一句「导出太难用了」——
表单逼他一次性写清楚，写不清楚就带着含糊往下走，
后面拆解、实现全按错的理解做完，到人工审核才发现方向不对。
（`clarify` 环节能补问，但那已经是流程里了 —— 工位、会话、编号都占上了。）

**改**：需求进流程**之前**先谈。
- `state="draft"` + `stage="intake"`，**不入流水线**，不上看板，不占并行工位
- `POST /projects/{slug}/intake` —— 只要一句大白话
- `refine_draft` handler：AI 读代码库 → 提问（≤3 个）→ 挂起等回答 →
  谈成型后输出 ```需求稿``` 块（标题/背景/要做什么/验收标准），回写到 title/body
- 谈满 3 轮强制出稿 —— 让人改比让人一直答问题强
- `PATCH /requirements/{id}` 人可以直接改稿（AI 写的不一定对）
- `POST /requirements/{id}/submit` 确认 → `active` + `triage`，这才进流程

`parse_draft()` **必须认出真的需求稿块**：硬把整段回复塞进 body 的话，
提问也会被当成需求稿，人还没回答就被推去确认。
确认时要把草稿阶段挂起的 job 作废 —— 留着它会在人回话时把已进流程的需求拽回草稿态。

前端「＋ 提需求」改成「＋ 立需求」，进的是对话页而不是表单弹窗；
需求稿实时显示、可直接编辑、确认才进流程。

**回归测试**：后端 16 个（草稿不进流水线 / 不上看板 / 回话只推进立需求 /
提问不被误认成需求稿 / 三轮封顶 / 确认后作废挂起 job / 不能改已进流程的 / 重复确认 409 /
租户隔离 …），前端 5 个。

## 第三轮验证

| | |
|---|---|
| 后端测试 | **262 passed** |
| 前端测试 | **53 passed**，TypeScript 干净，生产构建通过 |
| 接缝守卫 | ✓ 5 层完好 |
| 线上实测 | 立需求 → 草稿不上看板 → 人改稿 → 确认进流程 → 上看板 → 重复确认 409，全通 |

---

# 第四轮：把 agent 的思考流到页面上（2026-08-25）

界面上只有一句「正在看代码…」，一等好几分钟 —— 人不知道它在干嘛、
干到哪了、还是已经卡死了。要的是 opencode 的思考过程实时流出来、可展开。

## 先测清楚 opencode 到底怎么吐

不猜，实测（opencode 1.17.7）：

| 问题 | 实测结论 |
|---|---|
| `--format json` 是不是实时逐行吐？ | **是**。一次运行里各行到达时间跨 16 秒（`...970`→`...986`） |
| 事件长什么样 | `step_start` / `tool_use` / `step_finish` / `text`；工具信息在 `part.state{status,title,input,output}` |
| `opencode serve` 有没有流式接口 | 有，`GET /event` 是真 SSE；但**CLI 的活动不会出现在 serve 的 /event 上**（两个进程各自的总线） |

早先一次「跑了 4 分钟 stdout 一个字都没有」不是缓冲，是**模型挂住**了 ——
换个能用的模型立刻就有输出。差点据此得出错误结论。

## K1 等跑完再吐 → 改成边跑边推

`send()` 原来 `await proc.communicate()`，跑完才解析。改成 `_pump()`
逐行读 stdout，每行转成事件回调出去。

两个坑：
- **stdout / stderr 必须并发读**。只读 stdout 的话 stderr 管道写满（64KB）
  就把 opencode 卡死，表现成「超时」，查半天查不出原因。
- **推流失败不能带走这次运行**。回调只是给人看的，`try/except` 兜住。

## K2 思考混进了答案 — 严重（我自己引入的）

解析器现在给 `step`/`tool` 也带了给人看的文字（"开始一轮"、"读文件：x"），
而 `reply.text` 是 `"\n".join(所有事件的 text)` —— 于是**需求稿正文变成了
一串工具调用记录**。真实运行里业务员看到的「需求稿」是：

    开始一轮\n搜代码：fee|手续费\n找文件\n这一轮结束\n开始…

改成 `text` 只由 `kind == "text"` 的事件拼成。思考走另外两条路：
实时通道 + 消息的 `trace`。这条是 lumin-agent 的设计里已经写明的
（"reasoning ... is NOT aggregated into the persisted answer"），
我没照做才踩到。

## K3 跨进程实时根本不存在 — 严重

**worker 和 API 是两个进程。** 而：

- `publish_live` 原本只发进程内队列 → 思考永远到不了浏览器
- 更早就存在的洞：`dispatch()` 往 Redis `xadd`，但**没有任何地方 XREAD** ——
  写进去没人读。订阅者只看得到连接那一刻从 MySQL 补的历史，
  worker 之后发的一条都收不到。**整个实时通道是摆设。**

改：`publish_live` 也 `xadd`；`subscribe` 起一个 `_pump_redis` 消费。
配套三个坑，每个都有回归测试：

1. **自己发的会从 Redis 回来** → 持久事件靠 id 去重，临时事件 id 都是 0
   去不了重，页面上每条思考出现两遍。加 `src` 实例标记挡掉。
2. **`XREAD BLOCK 15000` 超过 redis 客户端默认 socket 超时** → 抛
   `TimeoutError`，我当成致命错误 return，**第一个安静窗口就把推流永久掐断**。
   实测就是这样：worker 那边 Redis 里躺着 16 条，页面上一条没有。
   超时是「这段时间没消息」，continue；同时把 socket 超时设得比 block 大。
3. **`pump` 变量作用域** → 客户端在回放历史那几行里就断开的话，
   `finally` 在赋值之前跑到，`UnboundLocalError` 把真正的断开原因盖掉。

## K4 SSE 具名事件前端一条都收不到 — 严重

服务端发的是 `event: status` 这种**具名事件**，而前端挂的是 `onmessage`
—— `onmessage` 只在事件没有名字时触发。所以那个「已经修好的实时刷新」
其实一条都没收到过，界面还是靠人手动刷。改成 `addEventListener`。
（变异测试确认：改回 `onmessage`，3 个测试立刻红。）

## K5 临时事件不能带 SSE id

浏览器 `EventSource` 会把收到的最后一个 id 记成 `lastEventId`，断线重连时
带回来。临时事件 id 是 0，带上就把游标重置成 0 —— **重连后整条历史重放一遍**。
`Emitted.sse()` 对 ephemeral 不输出 `id:` 行。

## K6 reaper 归档永远失败 — 严重（线上跑起来才暴露）

`signals.job_id` 外键指向 `jobs.id`，归档时只删了 `steps` 没删 `signals`
→ `Cannot delete or update a parent row`，整批回滚。而**收过信号的 job
恰恰是最常见的那种**（人工闸门、澄清挂起、重试唤醒都会留信号），
于是归档永远失败、reaper 崩溃重试，热表只增不减 ——
MySQL 没有部分索引，热表撑大正是 §7.5 要靠归档避免的那件事。

## 顺带

- **工具输出截断到 800 字**。实测一次 `read` 吐了 4000+ 字整个文件转储，
  全塞进事件会把 SSE 和面板都撑爆；人在思考面板里要看的是「它读了什么」，
  不是文件内容。
- **迁移校验只比表名，不比列** → 给已有表加一列能悄悄溜过去，新环境按
  0001 建出来就少这一列，跑起来才报 Unknown column。改成逐表比列，
  并验证过它真能拦住（故意删掉 `messages.trace` → 测试立刻红）。
- **宽屏上正文不再拉满**（1180px 上限，看板除外 —— 它横向滚，列越多看到越多）。

## 参考 lumin-agent 学到的

用户指的 `/Users/weizhanhao/lumin-agent` 里有几条已经踩过的经验：

1. **思考不进答案**（K2 正是没照做才踩到）
2. **一次运行独立于任何客户端连接** —— 后台任务往缓冲区追加，
   `GET /events` 只是订阅者；客户端断开不影响运行。我的形态一致
   （运行在 worker、缓冲在 bus），✓
3. **fan-out 用共享 append-only list + Condition，不用每订阅者一个队列** ——
   队列溢出会丢掉终止哨兵，客户端永远停在 "running"。我的队列无界不丢，
   且终止信号走持久化的 `status` 事件（可回放），不受影响
4. **不把上游原始错误抛给客户端**（固定文案 + 服务端记全）。
   **我这里故意不同**：这是内部研发平台，看得到的人就是这个空间的成员，
   而「失败原因无处可查」正是上一轮修掉的问题。保留原文是有意为之。
5. `opencode serve` 的 `message.part.delta`（`field: text|reasoning`）
   是**逐 token 的**，比 CLI 的 part 级事件细得多。**这是下一步该走的路**，
   但换 serve 模式要重做会话生命周期和端口池，这轮没做。

## 第四轮验证

| | |
|---|---|
| 后端测试 | **279 passed** |
| 前端测试 | **57 passed**，TypeScript 干净，构建通过 |
| 接缝守卫 | ✓ 5 层完好 |
| 变异测试 | 改回 `onmessage` → 3 红；删掉 `messages.trace` 列 → 迁移校验红 |
| 真 opencode | 流式回调在 +46.3s / +47.5s / +47.8s 到达，运行还没结束就推出来了 |
| 真 worker → Redis | 19 条真实事件，含对 doBuyRight 的真实 `read` / `grep` |
| Redis → SSE → 客户端 | 另一进程发 3 条，浏览器端全部实时收到，无 `id:` 行 |

## 这轮没能验证的

**一次完整的真 agent 运行 + 浏览器全程盯着**没跑通：免费模型
（`opencode/x-preview-f-free`）会随机挂死 —— 同一个 prompt 有时 16 秒出结果，
有时 10 分钟一个字没有，纯 shell 调用也一样。链路上每一跳都单独验证过了
（CLI 流式 ✓、worker→Redis ✓、Redis→SSE→客户端 ✓），但**四跳连起来跑通那一次
我没看到**。等接上稳定模型再补。

## 内置 ego-browser（浏览器自检）

`citrolabs/ego-lite` 本身就是一个 **Agent Skill**（`ego-browser`），
正好接进已有的可插拔 skill 层，不用为它开特例。

- **第二个上游**：`sync-ego.sh` → `vendor-ego/`，独立记 SHA
  （`sync.sh` 会 `rm -rf vendor` 整体重建，混进去会被抹掉）
- **新环节 `browser_check`（浏览器自检）**，排在预览之后、人工审核之前 ——
  `verify` 跑 lint/test/build，那些全过页面照样可能白屏、按钮点不动、接口 404
- 发现 `[严重] …` 就**拦住**，不放进人工审核（放过去等于让人去发现白屏）
- 结论落进需求对话，审核的人看得到它点了什么

选它的理由不只是「能自动化」：ego lite 的 **task space 天生隔离** ——
agent 在自己的空间里操作，不抢用户的标签页，又能复用用户已有的登录态。
跟本平台「每条需求一个隔离单元」是同一个思路。

**限制（如实说）**：ego lite 只有 macOS 版，且是**宿主上的桌面应用**，
容器里的 agent 够不着。所以宿主模式（`VP_DISABLE_CONTAINERS=1`）真跑，
容器模式探不到就**如实标「跳过」**——绝不把没检查说成通过。
本机还没装这个 app，所以**真正驱动浏览器那一次我没跑过**，
跑通的是环节接线、降级、拦截、落对话（8 个测试）。

顺带修掉一个脆测试：`test_pipeline_config_takes_effect_without_restart`
把环节数写死成 12，加一环就无意义地红一次。改成跟默认流水线比 ——
它要测的是「改了立刻生效」，不是默认有几环。
