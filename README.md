# doskill

AI 原生低代码平台 MVP：业务员在自己用的 web 产品页面上**框选一块区域**、用自然语言说出业务需求；浏览器扩展捕获 → Orchestrator 驱动 AI dev runner 在真实代码库的隔离分支上改代码 → 起 Docker 预览 → 业务员看预览、确认合并。程序员只维护「可编辑表面」与系统本身。

设计文档：[`docs/superpowers/specs/2026-05-14-ai-native-low-code-design.md`](docs/superpowers/specs/2026-05-14-ai-native-low-code-design.md)

## 目录结构

```
demo/         Plan 1：被改的目标产品（订单管理 mini app，React+Vite + FastAPI+MySQL）
orchestrator/ Plan 2 + 3：FastAPI 单体 + 4 个 Adapter（fake + 真实实现）
extension/    Plan 4：MV3 Chrome 扩展（业务员侧）
deploy/       Plan 5：ECS 部署脚本 + systemd unit + LiteLLM 代理
docs/
  superpowers/specs/   设计文档
  superpowers/plans/   5 份实现计划（一份对一阶段）
  mockups/             业务员交互的 HTML 静态演示
  RUNBOOK.md           运维手册
```

## 5 个 plan（一阶段一计划，串行）

1. **Plan 1 — Demo 应用** — React+Vite+RR + FastAPI + MySQL 的订单管理 mini app，已合并 main。
2. **Plan 2 — Orchestrator 骨架** — FSM + REST + SSE + git 管理 + 配额 + reaper，4 个 adapter 接口 + fake 实现。
3. **Plan 3 — Adapter 真实实现** — ReactViteStackAdapter / DockerPreviewAdapter / Claude+OpenCodeDevRunner / BrainstormingSkill。
4. **Plan 4 — 浏览器扩展** — MV3 + 7 个 panel + REST/SSE 客户端。
5. **Plan 5 — ECS 部署 + 真实 E2E** — deploy 脚本 + systemd + LiteLLM proxy + RUNBOOK。

每个 plan 在自己的 branch 上，按顺序叠在前一个上：`plan2-...` → `plan3-...` → `plan4-...` → `plan5-...`。

## 快速本地体验（无需 ECS）

```bash
open docs/mockups/doskill-extension-demo.html
```

## 跑通真实闭环

参见 [`deploy/README.md`](deploy/README.md)。

## 测试

```bash
# Orchestrator 全套（Plan 2 + 3 契约 + Docker 真起）
cd orchestrator && venv/bin/pytest

# 扩展
cd extension && npm install && npm test
```

## 设计原则

设计文档 §3.3：状态机驱动、分支+容器即隔离单位、Adapter 是唯一的「栈/工具相关」代码。换栈 / 换 dev runner / 换预览方式只动对应 adapter，Orchestrator 主体不变。

安全、多角色、多租户、多栈：明确 OUT（设计 §8）。
