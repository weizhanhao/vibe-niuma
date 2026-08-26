---
name: decompose-critic
description: Review a proposed requirement decomposition before it runs. Use when another skill has drafted a set of parallel tasks and needs them checked for missed surfaces, undeclared dependencies, missing cross-repo contracts, and wide refactors wrongly forced into vertical slices. Returns a structured pass/fail verdict.
---

# Decompose Critic

复核一份**已经拟好**的需求拆解，在它真正开跑之前。

这个 skill 存在的理由：平台的拆解是**全自动**的（无人工确认闸门）。
自动拆解在真实项目里最常见的翻车方式是「拆出来的子任务之间有隐藏依赖，
拆解 agent 自己看不见」。这一步就是补那个洞。

**你不是在重拆。** 你在找拆解者漏掉的东西。默认怀疑，找不到问题才放行。

## 输入

调用方会给你：

- ticket 列表：`Title` / `Blocked by` / `What it delivers`
- 每个 ticket 的 `Repos` 与 `Touches`
- 提取出的 `Contracts`
- 需求原文 + 澄清问答

## 检查项

逐条检查，每条给出结论与证据。

### 1. 覆盖完整性

需求原文和澄清问答里提到的每一处行为改变，是否都落到了某个 ticket 上？

**做法**：把需求拆成一组可验证的行为陈述，逐条找它落在哪个 ticket。
找不到归属的，就是漏了。

### 2. 隐藏依赖

有没有 ticket A 实际需要 ticket B 先完成，但 `Blocked by` 没写？

**最常见的三种**：

- **共享类型 / schema**：A 要用 B 新加的字段或类型
- **共享常量 / 枚举**：两侧各写一份，注定漂移
- **调用方 / 被调方**：前端 ticket 调的接口由后端 ticket 提供

**做法**：两两比对 `Touches`。相交的一对，必须要么有 `Blocked by` 边，
要么有 `Contracts` 解耦，要么合并成一个 ticket。三者都没有 → 隐藏依赖。

### 3. 跨仓契约

跨 `Repos` 的 ticket 之间，有没有先固定接口契约？

没有契约的跨仓并行**一定**会在合并期撞车。这是并行开发的经典失败模式。

**判定**：任意两个 ticket 的 `Repos` 不相同、且它们在功能上有调用关系，
则必须存在覆盖该调用的 `Contracts` 条目。

### 4. wide refactor 误判

有没有把一个 **wide refactor** 硬塞成垂直切片？

wide refactor = 一次机械改动（重命名列、改共享类型），blast radius 扇出全仓，
一次编辑同时打断几千个调用点，**任何垂直切片都无法单独变绿**。

**信号**：某个 ticket 的 `Touches` 覆盖大量文件且改动性质单一（重命名 / 改类型 /
换签名），却没有 `Sequence: expand | migrate | contract` 标记。

**判定 fail**，并要求改写成 expand → 分批 migrate → contract 序列。

### 5. 切片粒度

每个 ticket 是否真的是垂直切片 —— 切穿所有层、可独立演示、装得进一个 fresh
context window？

横向切片（「先把所有 schema 改完」）不算，它无法独立验证。

## 输出

只输出这个结构，不要额外散文：

```json
{
  "pass": true,
  "round": 1,
  "checks": {
    "coverage":        {"ok": true,  "note": "..."},
    "hidden_deps":     {"ok": true,  "note": "..."},
    "contracts":       {"ok": true,  "note": "..."},
    "wide_refactor":   {"ok": true,  "note": "..."},
    "slice_granularity": {"ok": true, "note": "..."}
  },
  "required_changes": [
    "T1 与 T3 都触达字段白名单定义 —— 提取为 Contracts 条目，两侧各自基于契约实现"
  ]
}
```

`pass` 为 false 时，`required_changes` 必须**具体到可执行**：说清改哪个 ticket、
怎么改。不要写「建议再看看依赖关系」这种没法照做的话。

## 停止条件

调用方连续 2 轮拿到 `pass: false` 就会停止拆分，把整块工作作为单个 ticket
串行执行。所以**第 2 轮的 `required_changes` 要格外具体** —— 那是最后一次机会。
