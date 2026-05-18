# Orchestrator（骨架）

AI 原生低代码平台的大脑 —— 一个 FastAPI 单体服务，把变更请求驱动过完整 FSM
（`created → clarifying → located → coding → building → preview-ready → merged`）。

**Plan 2 阶段：** 4 个 adapter 用 fake 实现装配，编排机器（状态机、REST、SSE、
git、配额、回收器）是完整真实的。Plan 3 把 fake 换成真实 adapter —— 唯一改动点是
`main.py` 的 `AppState.build_pipeline`。

## 运行

需要一个 MySQL（复用 demo 的容器 `demo-mysql`，`localhost:3307`，root/demopass）：

```bash
docker start demo-mysql || docker run -d --name demo-mysql \
  -e MYSQL_ROOT_PASSWORD=demopass -p 3307:3306 mysql:8
docker exec demo-mysql mysql -uroot -pdemopass \
  -e "CREATE DATABASE IF NOT EXISTS orchestrator; CREATE DATABASE IF NOT EXISTS orchestrator_test;"

cd orchestrator
python3 -m venv venv && venv/bin/pip install -e ".[dev]"
venv/bin/uvicorn orchestrator.main:app --port 9000
```

## REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/change-requests` | 创建变更请求，后台启动流水线 |
| GET | `/change-requests/{id}` | 查当前状态（SSE 断了拉这个） |
| GET | `/change-requests/{id}/events` | SSE 状态流（含历史回放） |
| POST | `/change-requests/{id}/answer` | 回答澄清问题 |
| POST | `/change-requests/{id}/merge` | preview-ready → 合并分支 |
| POST | `/change-requests/{id}/discard` | 丢弃（删分支、释放槽位） |
| POST | `/change-requests/{id}/retry` | failed/expired → 创建新请求重跑 |

## 测试

Plan 3 后测试分了若干层，按 marker 选：

```bash
cd orchestrator
venv/bin/pytest                              # 全跑（含 docker / slow），无 docker 自动 skip
venv/bin/pytest -m "not docker and not slow" # 最小快测（默认 CI）
venv/bin/pytest -m docker                    # 真起容器（需要 Docker daemon）
venv/bin/pytest -m slow                      # 慢测：npm build 等
VIBE_NIUMA_E2E=1 venv/bin/pytest -m e2e         # 真实端到端冒烟（需 LLM key）
```

## Adapter 装配（Plan 3）

`AppState.build_pipeline` 默认装配真实 adapter：

| 角色 | 实现 | 关键配置 |
|---|---|---|
| InteractionSkill | `BrainstormingSkill` | `LLM_API_KEY`, `VISION_MODEL`, `ANTHROPIC_BASE_URL` |
| StackAdapter | `ReactViteStackAdapter` | `DEMO_REPO_PATH` |
| DevRunnerAdapter | `ClaudeCodeDevRunner` 或 `OpenCodeDevRunner` | `DEV_RUNNER`, `DEV_MODEL`, `LLM_API_KEY`, `ANTHROPIC_BASE_URL` |
| PreviewAdapter | `DockerPreviewAdapter` | `PREVIEW_PORT_MIN/MAX`, `PREVIEW_HOST`, `DOCKER_NETWORK` |

`pipeline_factory` 可以被注入（测试用 fake；ECS 上用真实），是 Plan 3 的接线点。

## 真实 E2E 冒烟

`VIBE_NIUMA_E2E=1` 才跑。前提：MySQL 可连、Docker 可用、`claude` 或 `opencode` CLI 已装、
`LLM_API_KEY` + `ANTHROPIC_BASE_URL` 配好（见 `.env.example`），demo 仓库已 clone 到
`DEMO_REPO_PATH` 且有 `main` 分支。

## 架构

- `states.py` — FSM 定义（状态、合法转换、终态）
- `repository.py` — ChangeRequest 的 MySQL 持久化 + 受 FSM 约束的状态转换
- `git_manager.py` — 对目标仓库的真实 git 操作（切分支/提交/rebase/合并/删分支）
- `events.py` — 每请求一个 SSE 事件总线（含历史回放）
- `quota.py` — 并发槽位信号量（幂等 release）
- `interaction_channel.py` — adapter「问业务员」↔ SSE/answer 端点的桥
- `pipeline.py` — FSM 驱动器，串起 clarify→locate→run→build/serve
- `reaper.py` — 闲置 preview-ready 的回收
- `adapters/` — 4 个 Protocol 接口 + 共享类型 + fake 实现
- `main.py` — FastAPI app、REST + SSE 端点、lifespan（重启恢复 + reaper 启动）
