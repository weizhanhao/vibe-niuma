# platform-skills

流水线各环节的 Agent Skill 底座。对应设计文档
[`§14 Skill 层`](../docs/superpowers/specs/2026-08-24-v2-parallel-platform-architecture.md)（D12）。

## 为什么有这个目录

D8 把**流程**做成了声明式 DAG，但环节**内部**做什么原本还是硬编码 prompt。
Agent Skills 把这层也抽出来：**stage → skill 名字写在 Pipeline YAML，skill 本体是文件。**

换一个环节的实现 = 换一个 skill 文件，不动 orchestrator 代码。

## 布局

```
vendor/     上游原样，禁止手改。由 sync.sh 重新拉取
patches/    我们对上游的改动，unified diff
overlay/    我们自己写的 skill，不来自上游
dist/       build.sh 产出 = vendor + patches + overlay
```

`dist/` 就是烘焙进 workspace 容器镜像 `~/.config/opencode/skills/` 的内容
（§14.3 的 **L1 平台级**）。

## 上游

- 来源：[`mattpocock/skills`](https://github.com/mattpocock/skills)（MIT）
- Pin：见 `UPSTREAM.sha` / `UPSTREAM.version`
- **不直接依赖上游** —— 上游会改而我们要 fork，所以 vendored + 打 patch

只取映射到流水线环节的 skill，没有整包搬 36 个。

## 环节映射

| 流水线环节 | skill | 来源 |
|---|---|---|
| 需求分诊 | `triage` | vendor |
| 澄清 | `grilling` / `grill-with-docs` | vendor |
| 产出规格 | `to-spec` | vendor |
| 拆解 | `to-tickets` | vendor + patch |
| 拆解复核 | `decompose-critic` | **overlay（自研）** |
| 超大需求规划 | `wayfinder` | vendor |
| 实现 | `implement` / `tdd` | vendor |
| 失败自愈 | `diagnosing-bugs` | vendor |
| 审查·规格轴 | `code-review` | vendor |
| 解冲突 | `resolving-merge-conflicts` | vendor |
| 领域词汇 | `domain-modeling` | vendor |
| 模块设计 | `codebase-design` | vendor |

> 审查还有**缺陷轴**走 [`alibaba/open-code-review`](https://github.com/alibaba/open-code-review)
> 的 `ocr` CLI（§9），不在这里。两轴互补：`ocr` 查缺陷，`code-review` 查是否做到规格 + 是否合规范。

## 我们打了什么 patch

### `0001-to-tickets-auto-decompose.patch`

三处改动，全部有具体理由：

1. **第 4 步 `Quiz the user` → `decompose-critic`**
   上游要求人工确认拆解结果，与 **D6（AI 自动拆，不强制人工确认）** 冲突。
   改为交独立 critic 复核，连续 2 轮不过则降级为单 ticket 串行。

2. **第 5 步强制 local files 模式**
   上游默认发往 GitHub Issues / Linear。我们的 tracker 是平台自己的
   `Requirement` / `Task` 表，orchestrator 读 `.scratch/<slug>/issues/*.md` 入库。

3. **ticket 模板补 `Repos` / `Touches` / `Contracts` / `Sequence`**
   `Touches` 是 §8 冲突前置的输入，没有它调度器无从判断哪些任务能并行。

   > 上游写「避免具体文件路径，它们过期很快」—— 那条针对的是**实现指令**。
   > `Touches` 不是实现指令，是**调度元数据**。预测不准只影响调度质量，不影响正确性。

   `Sequence: expand | migrate | contract` 用于标记 wide refactor，
   让平台知道这串 ticket 的 `Touches` 大面积相交是预期的，别按普通冲突规则卡住。

## 用法

```bash
./build.sh          # vendor + patches + overlay → dist/
./sync.sh           # 按 UPSTREAM.sha 重新拉 vendor/
./sync.sh <新 sha>  # 升级上游；之后跑 build.sh 看 patch 是否还能应用
```

**patch 冲突 = 上游改了对应段落**，需要人工重做 patch。这是有意的：
静默 fallback 会让我们的改动悄悄失效。

## 两条纪律（照抄上游 `vendor/_invocation-convention.md`）

1. **skill 之间只经 Skill 工具互调** —— 禁止 `../other-skill/FILE.md` 跨目录引用，
   只写 `Call the Skill tool with "grilling"`。这是可插拔的封装保证：
   换掉一个 skill 不会扯断别人。

2. **编排级 skill 标 `disable-model-invocation: true`** —— 只能被显式调用，
   模型不自动触发。环节是 DAG 引擎确定性调度的，不是模型自己决定要不要跑。
   （被它们调用的 skill 如 `decompose-critic` 必须是模型可调的，不能标这个。）

## 三层安装位置

opencode 的发现顺序，项目级从 cwd 往上走到 git worktree 根：

| 层 | 位置 | 装什么 |
|---|---|---|
| L1 平台级 | 容器镜像 `~/.config/opencode/skills/` | 本目录的 `dist/`。不污染客户仓库 |
| L2 空间级 | worktree 的 `.opencode/skills/` | 每个空间自己的规范，从 Project 配置注入 |
| L3 仓库自带 | 客户仓库的 `.claude/skills/` | 天然被发现，**优先级最高** |

客户仓库自己的规范覆盖平台默认 —— 这正是想要的行为，优先级顺序不用调。

## 两个上游

| 上游 | 内容 | 同步 | SHA |
|---|---|---|---|
| `mattpocock/skills` | 研发流程 skill（triage / to-tickets / tdd / code-review …） | `./sync.sh` | `UPSTREAM.sha` |
| `citrolabs/ego-lite` | `ego-browser` —— 浏览器自动化 | `./sync-ego.sh` | `UPSTREAM-EGO.sha` |

两个 upstream 各管各的 vendor 目录、各记各的 SHA，谁升级都不影响对方。
`sync.sh` 会 `rm -rf vendor` 整体重建 —— 所以 ego 必须放在 `vendor-ego/`，
混进去会在下次同步时被抹掉。

### ego-browser 用在哪

流水线里的 **`browser_check`（浏览器自检）** 环节，排在预览之后、人工审核之前：
`verify` 跑的是 lint/test/build，那些全过，页面照样可能白屏、按钮点不动、
接口 404。这一环让 agent **像个人一样真去点一遍**。

ego lite 的 task space 本身就是隔离的 —— agent 在自己的空间里操作，
不抢用户的标签页，又能复用用户已有的登录态。跟本平台「每条需求一个隔离单元」
是同一个思路。

**限制（如实说）**：ego lite 目前只有 macOS 版，而且是**宿主上的桌面应用**——
容器里的 agent 够不着它。所以：

- 宿主模式（`VP_DISABLE_CONTAINERS=1`）：这一环真跑
- 容器模式：探不到 `ego-browser`，如实标「跳过」，**不会**把没检查说成通过

装它：`npx skills add citrolabs/ego-lite`，或下 macOS 客户端（见上游 README）。
