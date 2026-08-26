"""流水线执行器 —— 把 DAG 的每个环节接到具体能力上。

**这里是 D12 的兑现点**：环节内部做什么由 skill 决定，编排层只负责
「在这个 stage 告诉 agent 调用哪个 skill」。加环节改 YAML，换实现换 skill 文件，
两者都不动这个文件。

依赖一律**构造注入**，不在这里 import 具体实现 —— 接缝守卫（scripts/check_seams.py）
会拦住违规。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from vplatform.core.models import (
    Finding as FindingRow, Requirement, Run, Task, TaskTouch,
)
from vplatform.orchestration.dag import Pipeline, Stage, default_pipeline
from vplatform.orchestration.worker import JobContext, Registry

logger = logging.getLogger(__name__)

registry = Registry()


@dataclass
class Capabilities:
    """各层能力的注入口。测试传替身，生产传真实现。"""
    workspace: object | None = None      # WorkspaceProvider
    agent: object | None = None          # AgentSession
    reviewer: object | None = None       # CodeReviewAdapter
    finding_filter: object | None = None
    deployer: object | None = None       # DeployAdapter
    host: object | None = None           # GitHostAdapter —— 没有它改动推不出工位
    pipeline: Pipeline | None = None

    def pipe(self) -> Pipeline:
        return self.pipeline or default_pipeline()


_caps = Capabilities()
_factory = None       # CapabilityFactory —— 由 bootstrap.install() 注入


def configure(caps_or_factory) -> None:
    """装配入口。

    传 Capabilities → 全局固定能力（测试用）。
    传 CapabilityFactory → **按 project 现取**（生产用）。

    为什么要 factory：不同空间有不同的仓、模型、密钥。全局单例只能服务
    一个空间，那就不叫多租户平台了。
    """
    global _caps, _factory
    if hasattr(caps_or_factory, "for_project"):
        _factory = caps_or_factory
    else:
        _caps = caps_or_factory
        _factory = None


def caps_for(session, project_id: str) -> Capabilities:
    """取该空间的能力。没装配 factory 时回落到全局（测试路径）。"""
    if _factory is None:
        return _caps
    from vplatform.core.models import Project
    project = session.get(Project, project_id)
    if project is None:
        return _caps
    return _factory.for_project(project)


# **思考过程也要用中文。**
# 这套东西的思考是直接流到页面上给业务员看的，模型默认用英文推理
# （实测 deepseek 的 reasoning 全是英文）—— 看不懂的思考等于没有思考。
_LANG = ("用中文思考和回答。**包括你的推理过程（reasoning）本身也要用中文**，"
         "不要用英文思考再翻译。代码、标识符、命令保持原样。")


def skill_prompt(stage: Stage, *, context: str) -> str:
    """把 stage 的 skill 声明变成给 agent 的指令。

    照抄 mattpocock/skills 的 `.agents/invocation.md` 约定：**显式说「调用 Skill 工具」**，
    而不是丢一个 `/name` 让模型自己揣摩 —— 后者命中率低得多。
    """
    lines = [_LANG]
    if stage.skill:
        lines.append(f'Call the Skill tool with "{stage.skill}".')
    lines.append(context)
    if stage.critic:
        lines.append(
            f'完成后把结果交给复核：Call the Skill tool with "{stage.critic}". '
            f"复核返回 pass:false 就按它的意见修订后重新提交；"
            f"连续 2 轮不过则停止拆分，整块作为单个任务输出并标 Degraded:true。"
        )
    return "\n\n".join(lines)


@registry.register("refine_draft")
async def refine_draft(ctx) -> None:
    """把草稿聊成型。**这一段在流水线之外** —— 不推进 stage、不占并行工位。

    草稿不上看板：谈到一半的东西不该跟真在跑的需求混在一起。
    """
    s = ctx.session
    req_id = ctx.payload["requirement_id"]
    req = s.get(Requirement, req_id)
    if req is None:
        raise LookupError(f"需求 {req_id} 不存在")
    if req.state != "draft":
        # 人已经点了确认，草稿轮次的残留 job 直接作废，别把它拽回草稿态
        return

    caps = caps_for(s, req.project_id)
    from vplatform.orchestration.stages import StageRunner

    # **step 名必须带上对话轮次。**
    # 不带的话：第一轮问完问题 → step 记成 done(`awaiting=True`) → 挂起；
    # 人答完唤醒，`ctx.step` 直接命中缓存又挂一次 —— agent 根本不会被调，
    # 草稿对话永远过不了第一轮。跟「打回改」那个死胡同是同一个坑，
    # 那次修了审核路径，没把教训用到对话路径上。
    out = await ctx.step(f"intake:{_talk_round(s, req_id)}",
                         lambda: StageRunner(caps, s).refine_draft(req))
    if out.get("awaiting"):
        ctx.emit("draft", state="awaiting_user", **out)
        ctx.park_for_signal()
        return
    ctx.emit("draft", state="ready" if out.get("ready") else "talking", **out)


@registry.register("advance_requirement")
async def advance_requirement(ctx: JobContext) -> None:
    """推进一条需求到下一个环节。

    每个环节一个 step —— step 幂等，所以 worker 被 kill 后重来会跳过已完成的环节，
    不会重复开工位、重复烧 token。

    **全程用 ctx.session**（worker 的事务），不自己开 session_scope。
    """
    s = ctx.session
    req_id = ctx.payload["requirement_id"]

    req = s.get(Requirement, req_id)
    if req is None:
        raise LookupError(f"需求 {req_id} 不存在")
    project_id, stage_key = req.project_id, req.stage
    pipe = caps_for(s, project_id).pipe()
    stage = pipe.get(stage_key)

    # 人工闸门：挂起等信号。**不占 worker** —— 工位已回收，别人能用
    if stage.is_human_gate:
        sig = ctx.take_signal("review_decision")
        if sig is None:
            ctx.emit("status", stage=stage_key, state="awaiting_human")
            ctx.park_for_signal()
            return
        decision = sig.payload.get("decision")
        ctx.emit("status", stage=stage_key, state="decided", decision=decision)
        if decision != "approve":
            if decision == "reject":
                req.state = "discarded"
                s.flush()
                return
            # **打回改必须真的能重跑。**
            # 之前只把 stage 改回 implement 就 return，没有入队任何 job ——
            # 需求永远停在「并行开发」，看板上显示在跑，实际没有 worker 碰它。
            # 而且幂等键 `req:{id}:implement` 已经存在且 done，补 enqueue 也没用，
            # 会直接返回那个 done 的 job。所以要**带轮次**重开幂等键，
            # 并清掉上一轮的 step 缓存（否则 ctx.step 会跳过所有环节）。
            req.state = "active"
            req.stage = "implement"
            rework = _bump_rework(s, req)
            _reset_steps_from(s, req.id, "implement")
            s.flush()
            ctx.store.enqueue(
                project_id=project_id, kind="advance_requirement",
                requirement_id=req_id,
                idempotency_key=f"req:{req_id}:implement:r{rework}",
                payload={"requirement_id": req_id, "stage": "implement"},
                lane="background")
            return

    ctx.emit("status", stage=stage_key, state="running")

    # 环节主体 —— 具体做什么由 skill / adapter 决定，这里只调度
    # 轮次进 step 名 —— 澄清环节会「问完挂起等人答」，缓存不带轮次的话
    # 人答完唤醒会命中上一轮的 `awaiting`，再挂一次，永远问不完。
    # 非对话环节的轮次不变，缓存照常生效（重放依然幂等）。
    result = await ctx.step(f"stage:{stage_key}:{_talk_round(s, req_id)}",
                            lambda: _run_stage(
        stage=stage, project_id=project_id, requirement_id=req_id, session=s))

    # **环节失败或被跳过就停下**，不能继续推进。
    #
    # 之前无条件 `req.stage = nxt.key` —— 拆解没产出、验证失败、合并受阻、
    # 甚至能力根本没注入的需求，照样一路走到人工审核页。审核人看到的是一条
    # 「正常跑完」的需求，批准后继续走到部署闸门。**零代码改动的需求被推到生产。**
    #
    # 「缺能力」也必须阻断而不是放行：跳过实现环节的需求没有任何资格进审核。
    if result.get("ok") is False:
        req.state = "failed"
        s.flush()
        ctx.emit("status", stage=stage_key, state="failed", **result)
        return
    if result.get("missing"):
        req.state = "blocked"
        s.flush()
        ctx.emit("status", stage=stage_key, state="blocked", **result)
        return

    # 环节说「在等人回话」→ 挂起，不推进。
    # 澄清不是人工闸门（`gate: auto`），但它提了问题就得等答案 ——
    # 不等的话问了等于没问，直接拿半懂的需求去拆解。
    if result.get("awaiting"):
        ctx.emit("status", stage=stage_key, state="awaiting_user", **result)
        ctx.park_for_signal()
        return

    nxt = pipe.next_of(stage_key)
    if nxt is None:
        req.state = "done"
    else:
        req.stage = nxt.key
    s.flush()

    ctx.emit("status", stage=stage_key, state="done", **result)

    if nxt is not None:
        ctx.store.enqueue(project_id=project_id, kind="advance_requirement",
                          requirement_id=req_id,
                          idempotency_key=f"req:{req_id}:{nxt.key}",
                          payload={"requirement_id": req_id, "stage": nxt.key},
                          lane="interactive" if nxt.is_human_gate else "background")


async def _run_stage(*, stage: Stage, project_id: str, requirement_id: str,
                     session=None) -> dict:
    """环节主体。

    有对应执行体就跑（stages.StageRunner），没有就是纯调度环节直接过。
    需要外部能力而能力未注入时**明确列出全部缺项并跳过**，
    而不是假装成功 —— 静默假成功是最难查的一类 bug。
    一次列全（不是报第一个就停），否则要来回试好几轮才知道还缺什么。
    """
    caps = caps_for(session, project_id) if session is not None else _caps
    missing: list[str] = []
    if stage.skill and caps.agent is None:
        missing.append("agent")
    if stage.needs_workspace and caps.workspace is None:
        missing.append("workspace")
    if stage.adapter == "ocr" and caps.reviewer is None:
        missing.append("reviewer")
    if stage.adapter == "deploy" and caps.deployer is None:
        missing.append("deployer")
    if missing:
        return {"skipped": f"环节 {stage.key} 缺少能力：{'、'.join(missing)}",
                "missing": missing}

    from vplatform.orchestration.stages import DISPATCH, StageRunner

    method = DISPATCH.get(stage.key)
    if method is None or session is None:
        return {"ok": True}

    req = session.get(Requirement, requirement_id)
    runner = StageRunner(caps, session)
    outcome = await getattr(runner, method)(stage, req)
    return outcome.as_dict()


def _talk_round(session, requirement_id: str) -> int:
    """这条需求上人说过几句话。

    用作 step 缓存的一部分：人每回一句，轮次 +1，缓存自然失效，
    环节会带着新信息重跑。不回话时轮次不变，重放仍然幂等。
    """
    from sqlalchemy import func as _f, select as _sel
    from vplatform.core.models import Message as _M
    return int(session.execute(
        _sel(_f.count(_M.id)).where(_M.requirement_id == requirement_id,
                                    _M.role == "user")
    ).scalar_one() or 0)


def _bump_rework(session, req) -> int:
    """返工轮次。幂等键要带上它，否则第二轮会命中第一轮那个 done 的 job。"""
    cfg = dict(getattr(req, "attachments", None) or [])
    n = int((req.contracts and 0) or 0)  # 占位，真实计数放 config
    from sqlalchemy import func, select as _sel
    from vplatform.core.models import Job as _Job
    n = int(session.execute(
        _sel(func.count(_Job.id)).where(
            _Job.requirement_id == req.id,
            _Job.idempotency_key.like(f"req:{req.id}:implement:r%"))
    ).scalar_one() or 0)
    return n + 1


def _reset_steps_from(session, requirement_id: str, stage_key: str) -> None:
    """清掉从某环节起的 step 缓存。

    不清的话 `ctx.step("stage:implement")` 会命中上一轮的缓存直接返回，
    返工等于什么都不做。
    """
    from sqlalchemy import delete as _del, select as _sel
    from vplatform.core.models import Job as _Job, Step as _Step
    job_ids = list(session.execute(
        _sel(_Job.id).where(_Job.requirement_id == requirement_id)).scalars())
    if job_ids:
        session.execute(_del(_Step).where(_Step.job_id.in_(job_ids)))
    session.flush()


def touch_conflicts(session, *, project_id: str, paths: set[str],
                    exclude_requirement: str | None = None) -> set[str]:
    """哪些 in-flight 需求与这批 touches 相交（§8.3 保险 ①）。

    **这是把冲突预防前置到调度期的兑现点** —— 不是等合并期再让 AI 收拾。
    走关联表 JOIN，MySQL 上有索引（§7.5 ③）。
    """
    if not paths:
        return set()
    stmt = (
        select(Task.requirement_id)
        .join(TaskTouch, TaskTouch.task_id == Task.id)
        .join(Requirement, Requirement.id == Task.requirement_id)
        .where(TaskTouch.project_id == project_id,
               TaskTouch.path.in_(paths),
               Requirement.state == "active")
    )
    if exclude_requirement:
        stmt = stmt.where(Task.requirement_id != exclude_requirement)
    return set(session.execute(stmt).scalars().all())


def wide_refactor_exempt(session, requirement_id: str) -> bool:
    """wide refactor 的 touches 大面积相交是**预期的**，不按普通冲突规则卡住（§8.4）。

    判据是任务上的 Sequence: expand|migrate|contract 标记 —— 由 to-tickets 打。
    """
    req = session.get(Requirement, requirement_id)
    if req is not None and req.sequence_kind:
        return True
    seqs = session.execute(
        select(Task.sequence).where(Task.requirement_id == requirement_id)
    ).scalars().all()
    return any(s in ("expand", "migrate", "contract") for s in seqs if s)
