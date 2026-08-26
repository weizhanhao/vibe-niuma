# v2 Demo

三个都能跑，前两个**不需要任何 API key**。

```bash
./run-demo.sh          # 工位隔离 + 冲突三档（秒级）
./run-demo.sh full     # 追加真实 AI 复核（需 DASHSCOPE_API_KEY）
./run-demo.sh serve    # 起后端 + 前端，浏览器里点
```

## Demo 1 · 并行工位隔离 —— M2 分水岭

5 个 Run 同时改**同一个文件的同一行**，互不可见。

v1 只有一个工作树，`create_branch` 会 `stash + reset --hard + clean` ——
第二个 Run 直接抹掉第一个 agent 正在写的文件。这里每条轨道各有各的 worktree，
共享一个 bare mirror 当 object store。

```
工位 1：自己的改动 在 · 别人的改动 没串进来
…
共享 object store：2 个 bare mirror 服务 5 个工位
结论：✓ 5 条轨道并行互不污染
```

## Demo 2 · 三档递进的冲突处理

`cr/2-t1` 给 `query()` 加 `store_id`，`vibe/dev` 同一行加 `limit` —— 真语义冲突。

```
◐ git         1 处冲突 git 无法自动解
◐ mergiraf    未安装 mergiraf，跳过（直接进 AI 档）
✓ ai          AI 解决 1/1 处（会话 ses_orig_a91c2f）

合并后：def query(status=None, store_id=None, limit=50) -> list[Order]
```

两边参数都保住了。**注意 mergiraf 未装时是「如实记录跳过」而不是静默略过** ——
静默会让人以为那一档跑过了。

> demo 里的 AI 解冲突用确定性替身。真实实现**携带原会话**（`ses_orig_…`）——
> 它知道自己当初为什么这么改。这与复核 agent 必须是全新会话正好相反。

## Demo 3 · 真实 AI 复核

在 `orders-api` 上埋一个违反 `CONTEXT.md` 约定的改动（金额从元改成分），
跑真实 `ocr` + 自建过滤层。

配置是实测定下来的（设计文档 §9.11）：
**DashScope 端点 + `--no-filter` + 自建过滤** —— 内置过滤贵 17 倍且在 DeepSeek 直连上静默失效。

### 真实输出（2026-08-24）

```
ocr：2 条原始发现 · 34,406 token · 59s

[保留] critical app/routers/orders.py:17
       金额字段从 o.amount_yuan（元）改为 o.amount_cents（分），违反了接口契约。
       ← 数值相差 100 倍，前端展示错误

[保留] high     app/routers/orders.py:21
       新增的 store_id 参数已接收但从未用于过滤：query(status) 只传了 status。
       ← 区域经理筛选无效，返回全量订单，造成权限绕过和数据泄露风险

结论：留 2 · 丢 0
```

**第二条是没被埋的真 bug。** 埋 bug 时我只改了函数签名没改函数体 ——
ocr 自己发现了，定级和失败场景都对。

**第一条说明 `--background` 是真的质量杠杆**：它是对着传进去的契约
（`amount 单位为元`）判的，不是靠猜。没有背景信息时这只是一处普通的字段改动。

## Demo 目标仓

`demo-target/` 下两个真 git 仓，各有 `main` 和 `vibe/dev` 分支：

- `orders-api` —— Python，订单接口 + 测试 + `CONTEXT.md` 领域词汇
- `orders-web` —— TypeScript/React，订单列表

它们**不是玩具占位**：`orders-api` 的测试能跑（`pytest` 3 passed），
`CONTEXT.md` 里写了「金额内部用分、出参转元」的约定 —— Demo 3 埋的 bug 正是违反它。

## serve 模式看什么

1. **需求池** —— 看板列由流水线配置推导，不硬编码
2. 点 **R-1**（并行开发中）—— 三仓契约解耦，每个任务的 `touches` 都列出来
3. 点 **R-2**（待审核）—— 两轴复核，含**被过滤掉的那条**（切「含被过滤的」）
4. 点 **R-5** —— wide refactor，`expand → migrate → contract`，不报冲突预警
5. **合并队列** —— per-repo 串行
6. **流水线** —— 每个环节由 skill 还是 adapter 实现，一眼看清

> `serve` 模式用 `VP_DEV_AUTH=1` 开发认证（`X-User` 头）。
> **生产必须关掉它**，走 `Authorization: Bearer <token>`；
> 不关的话任何人加个头就能冒充任意用户。
