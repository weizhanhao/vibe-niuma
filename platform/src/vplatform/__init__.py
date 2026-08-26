"""vibe-niuma v2 —— 企业级并行 AI 开发平台。

设计文档：docs/superpowers/specs/2026-08-24-v2-parallel-platform-architecture.md

分层（自下而上）：
    core/          数据模型 · 配置 · 事件总线        —— 不依赖任何具体实现
    orchestration/ jobs/steps/signals · worker · DAG 引擎
    workspace/     worktree + 容器隔离 · 端口租约
    agents/        AgentSession（opencode server）
    review/        CodeReviewAdapter（ocr）+ 自建过滤合并层
    merge/         合并队列 · 三档冲突处理
    deploy/        DeployAdapter · 环境分层
    hosts/         GitHostAdapter（GitHub / 将来 Codeup）
    skills/        Skill 层三层安装
    api/           FastAPI

**接缝纪律（CI 守）**：core / orchestration / workspace 不得 import
hosts.github / review.ocr 等具体实现，只能依赖 Protocol。
见 scripts/check_seams.py。
"""

__version__ = "0.2.0"
