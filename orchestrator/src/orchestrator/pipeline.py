"""Pipeline —— FSM 驱动器。把一条 `created` 的变更请求推过
clarify → locate → run → build/serve，每步写状态 + 推 SSE。

设计文档约束：
- 离开 `created` 前 acquire 配额槽位；进入失败终态时 release。
- 到 `preview-ready` 时 pipeline.run() 结束 —— 槽位仍被占用，由后续 merge/discard/expire 释放。
- 阻塞型 git 操作用 asyncio.to_thread 包装。
- coding 阶段只 create_branch，改代码+commit 委托给 dev_runner.run。

Phase F：每个 phase 注入一个绑定 (request_id, phase) 的 log 闭包，dev runner /
StackAdapter.build / PreviewAdapter.serve 把子进程每行输出实时通过 EventBus
publish_log，扩展端 StatusPanel 实时显示。Pipeline 自己也在关键节点发粗粒度 marker。

Phase D：到达 preview-ready 时把 spec/plan/result.md 沉淀到
<repo>/.doskill/history/cr-<id>/，供后续 LLM 会话回看「doskill 做过什么」。
写入失败不阻塞 pipeline。
"""
import asyncio
import logging
import subprocess
import traceback
from datetime import datetime


def _now_iso() -> str:
    return datetime.utcnow().isoformat()

from orchestrator.adapters.interfaces import (
    DevRunnerAdapter,
    InteractionSkill,
    PreviewAdapter,
    StackAdapter,
)
from orchestrator.adapters.types import RawRequest
from orchestrator.compaction import CompactionLLM, compact
from orchestrator.events import Event, EventBus
from orchestrator.git_manager import GitManager
from orchestrator.history_writer import write_history
from orchestrator.interaction_channel import SSEInteractionChannel
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State

logger = logging.getLogger(__name__)


def _git_diff_files(repo_path: str, branch: str) -> list[str] | None:
    """best-effort：git diff --name-only main..branch；任何异常返回 None。

    用于 Phase D 体量分级第 5 项：「dev runner 实际改了几个文件」。
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"main..{branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return [line for line in result.stdout.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001
        return None


def _make_log_sink(event_bus: EventBus, request_id: str, phase: str):
    """工厂：返回 await-able(line: str) → None；绑定 request_id+phase。"""

    async def _log(line: str) -> None:
        await event_bus.publish_log(request_id, phase, line)

    return _log


async def _with_heartbeat(
    awaitable,
    *,
    log,
    label: str,
    interval: float = 5.0,
):
    """跑 awaitable，每 interval 秒发一条 「⏳ <label> 已 Xs...」心跳。

    用于 LLM HTTP 调用 / git 操作这类没有 stdout 流的等待，避免 UI 长时间黑屏。
    awaitable 完成或抛异常时心跳自动取消并清理；返回 awaitable 的结果或重抛异常。
    """
    import time
    started = time.monotonic()
    done = asyncio.Event()

    async def _ticker() -> None:
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=interval)
            except asyncio.TimeoutError:
                elapsed = int(time.monotonic() - started)
                try:
                    await log(f"⏳ {label} 已 {elapsed}s...")
                except Exception:
                    pass

    ticker = asyncio.create_task(_ticker())
    try:
        return await awaitable
    finally:
        done.set()
        ticker.cancel()
        try:
            await ticker
        except BaseException:
            pass


class _PhaseError(Exception):
    """流水线某一步失败 —— 携带 phase / reason / log。"""

    def __init__(self, phase: str, reason: str, log: str):
        self.phase = phase
        self.reason = reason
        self.log = log
        super().__init__(f"{phase}: {reason}")


class Pipeline:
    def __init__(
        self,
        repo_path: str,
        repository: ChangeRequestRepository,
        git_manager: GitManager,
        event_bus: EventBus,
        quota: QuotaManager,
        interaction_skill: InteractionSkill,
        stack_adapter: StackAdapter,
        dev_runner: DevRunnerAdapter,
        preview_adapter: PreviewAdapter,
        compaction_llm: CompactionLLM | None = None,
        compaction_threshold_soft: int = 40_000,
    ):
        self.repo_path = repo_path
        self.repository = repository
        self.git_manager = git_manager
        self.event_bus = event_bus
        self.quota = quota
        self.interaction_skill = interaction_skill
        self.stack_adapter = stack_adapter
        self.dev_runner = dev_runner
        self.preview_adapter = preview_adapter
        # Plan 9 Task 5：compaction 入参可注入；缺省为 None → 用 _DefaultCompactionLLM
        # 联网。阈值默认 40k tokens（spec §动态压缩）。
        self.compaction_llm = compaction_llm
        self.compaction_threshold_soft = compaction_threshold_soft
        self._channels: dict[str, SSEInteractionChannel] = {}

    def channel_for(self, request_id: str) -> SSEInteractionChannel:
        """暴露某请求的交互通道，供 REST /answer 端点回填答案。"""
        return self._channels[request_id]

    async def _set_state(self, request_id: str, state: State) -> None:
        self.repository.transition(request_id, state)
        await self.event_bus.publish(
            request_id, Event(type="status", data={"state": state.value})
        )

    async def _maybe_compact_history(self, request_id, ctx, *, log) -> None:
        """Plan 9 Task 5：把 CR 所在 conversation 的 messages 压缩后塞 ctx.chat_history。

        - 没挂 conversation_id → noop（兼容老 CR / Legacy 流）
        - compact 产生新 summary（输出长度变了）→ 把新 summary append 到 conversation
        - 任何异常都吞掉 + log warning，绝不让历史问题阻塞主流程
        """
        try:
            cr = self.repository.get(request_id)
            if cr is None or not cr.conversation_id:
                return
            from orchestrator.conversation import ConversationRepository

            conv_repo = ConversationRepository(self.repository._db)
            conv = conv_repo.get(cr.conversation_id)
            if conv is None:
                return
            pre_messages = list(conv.messages or [])
            compacted = await compact(
                pre_messages,
                llm=self.compaction_llm,
                threshold_soft=self.compaction_threshold_soft,
            )
            # 找新 summary（compacted 多出来的 summary 行）
            pre_summary_contents = {
                m.get("content") for m in pre_messages if m.get("type") == "summary"
            }
            new_summaries = [
                m for m in compacted
                if m.get("type") == "summary"
                and m.get("content") not in pre_summary_contents
            ]
            for s in new_summaries:
                conv_repo.append_message(cr.conversation_id, s)
            ctx.chat_history = compacted
            if new_summaries:
                await log(
                    "coding",
                    f"▸ chat history 触发压缩：合并 {new_summaries[0].get('replaces_count', '?')} 条老 AI 消息为 summary",
                )
        except Exception:  # noqa: BLE001
            logger.warning("compaction 失败 cr=%s（不影响 pipeline）", request_id, exc_info=True)

    async def run(self, request_id: str) -> None:
        """驱动一条 `created` 请求。异常路径统一收敛到 _PhaseError → mark_failed。

        Phase F：在每个 phase 注入 log 闭包给 adapter，粗粒度 marker 在每步
        开头/结束发；细粒度行由 adapter 内 stream subprocess 时实时回灌。
        """
        import time
        bus = self.event_bus
        run_started = time.monotonic()
        phase_started: dict[str, float] = {}

        async def _phase_log(phase: str, line: str) -> None:
            await bus.publish_log(request_id, phase, line)

        async def _phase_start(phase: str, line: str) -> None:
            phase_started[phase] = time.monotonic()
            await _phase_log(phase, line)

        async def _phase_done(phase: str, line: str) -> None:
            t0 = phase_started.get(phase)
            elapsed = f" (耗时 {time.monotonic() - t0:.1f}s)" if t0 is not None else ""
            await _phase_log(phase, f"{line}{elapsed}")

        await self.quota.acquire(request_id)
        try:
            # created → clarifying
            await self._set_state(request_id, State.CLARIFYING)
            await _phase_start("clarifying", "▸ 读 AGENTS.md / 等 /init 就绪...")
            channel = SSEInteractionChannel(request_id, self.event_bus, phase="clarifying")
            self._channels[request_id] = channel
            cr = self.repository.get(request_id)
            raw = RawRequest(
                url=cr.url,
                screenshot_b64=cr.screenshot_b64,
                box_coords=cr.box_coords,
                viewport=cr.viewport,
                request_text=cr.request_text,
            )
            await _phase_log("clarifying", "▸ 问视觉模型判断业务意图（流式输出 ↓）...")
            clarify_log = _make_log_sink(bus, request_id, "clarifying")
            brief = await _with_heartbeat(
                self.interaction_skill.clarify(raw, channel),
                log=clarify_log, label="视觉模型回答中",
            )
            await _phase_done("clarifying", "✓ 澄清完成")

            # clarifying → located
            await _phase_start("locating", "▸ 解析路由配置...")
            locate_log = _make_log_sink(bus, request_id, "locating")
            locate_result = await _with_heartbeat(
                self.stack_adapter.locate(raw.url),
                log=locate_log, label="路由解析中",
            )
            if not locate_result.entry_files:
                raise _PhaseError(
                    "located", "no-route-match", f"URL 未匹配任何路由: {raw.url}"
                )
            await _phase_done(
                "locating",
                f"✓ 定位到入口：{', '.join(locate_result.entry_files[:3])}",
            )
            await self._set_state(request_id, State.LOCATED)

            # located → coding
            branch = f"cr/{request_id}"
            await _phase_start("coding", f"▸ 切分支 {branch}...")
            try:
                await asyncio.to_thread(self.git_manager.create_branch, branch)
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "coding", "git-error",
                    f"create_branch 失败：{exc}\n{''.join(traceback.format_exception(exc))}",
                ) from exc
            self.repository.set_branch(request_id, branch)
            # 同 preview-ready：branch 是新写入的，事件得夹带，否则 mirror 落空。
            self.repository.transition(request_id, State.CODING)
            await self.event_bus.publish(
                request_id,
                Event(type="status", data={
                    "state": State.CODING.value,
                    "branch": branch,
                }),
            )
            await _phase_log("coding", "▸ 打包代码上下文...")
            coding_log = _make_log_sink(bus, request_id, "coding")
            try:
                ctx = await _with_heartbeat(
                    self.stack_adapter.context_pack(locate_result, raw, brief),
                    log=coding_log, label="代码上下文打包中",
                )
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "coding", "context-pack-error",
                    f"context_pack 失败：{exc}\n{''.join(traceback.format_exception(exc))}",
                ) from exc
            # 把 phase=coding 的 log 闭包塞进 DevContext，runner 子进程行级回灌
            ctx.log = _make_log_sink(bus, request_id, "coding")

            # Plan 9 Task 5：把 conversation 历史经 compact() 注入 ctx.chat_history。
            # 阈值未到 → 原样塞；阈值到 → LLM 出 summary，append 回 conversation
            # 表（下轮直接命中、不重算）。任何异常吞掉 + log warning，不阻塞流水线。
            await self._maybe_compact_history(request_id, ctx, log=_phase_log)

            await _phase_log("coding", "▸ 起 dev runner（首次启动可能静默 30~60s，下方滚动是其 stdout）...")
            try:
                run_result = await self.dev_runner.run(self.repo_path, branch, ctx)
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "coding", "runner-error", "".join(traceback.format_exception(exc))
                ) from exc
            if not run_result.changed:
                raise _PhaseError("coding", "no-changes", run_result.log)
            # Plan 8 Task 12：commit_sha 多仓时是 dict，单仓时是 str。
            # 多仓 → 写到 ChangeRequest.repos JSON 列；log 显示精简版「{repo:sha8, ...}」。
            sha = run_result.commit_sha
            if isinstance(sha, dict):
                self.repository.set_repos(request_id, sha)
                display = "{" + ", ".join(f"{r}:{s[:8]}" for r, s in sorted(sha.items())) + "}"
                await _phase_done("coding", f"✓ 编码完成 (多仓) commit={display}")
            else:
                await _phase_done("coding", f"✓ 编码完成 commit={sha}")

            # coding → building
            await self._set_state(request_id, State.BUILDING)
            await _phase_start("building", "▸ npm run build...")
            build_log = _make_log_sink(bus, request_id, "building")
            build_result = await self.stack_adapter.build(
                self.repo_path, branch, log=build_log,
            )
            if not build_result.ok:
                raise _PhaseError("building", "build-failed", build_result.log)
            await _phase_log("building", "▸ docker build + run...")
            try:
                instance = await self.preview_adapter.serve(
                    self.repo_path, branch, log=build_log,
                )
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "building", "container", "".join(traceback.format_exception(exc))
                ) from exc

            # building → preview-ready
            self.repository.set_preview(
                request_id, url=instance.url, handle=instance.handle
            )

            # Phase D：preview 容器就绪后，落地 spec/plan/result.md 历史快照。
            # 任何异常吞掉 + 记 warning —— 历史写盘失败绝不阻塞流水线。
            try:
                now = time.monotonic()
                phase_timings = {
                    phase: now - t0 for phase, t0 in phase_started.items()
                }
                total_so_far = now - run_started
                files_changed = await asyncio.to_thread(
                    _git_diff_files, self.repo_path, branch,
                )
                await asyncio.to_thread(
                    write_history,
                    repo_path=self.repo_path,
                    request_id=request_id,
                    raw_request=raw,
                    brief=brief,
                    locate_result=locate_result,
                    branch=branch,
                    preview_url=instance.url,
                    phase_timings=phase_timings,
                    total_elapsed=total_so_far,
                    dev_runner_files_changed=files_changed,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "history_writer.write_history 失败 cr=%s（不影响 pipeline）",
                    request_id,
                    exc_info=True,
                )

            await _phase_done("building", f"✓ 预览容器就绪 {instance.url}")
            # 注意：_set_state 默认只发 {state}；这里 preview_url 是新写入 DB 的，
            # 不夹带在事件里扩展端的 mirror 永远停在 previewUrl=null。所以这里
            # 不用 _set_state，直接 transition + publish 一个带 preview_url 的事件。
            self.repository.transition(request_id, State.PREVIEW_READY)
            await self.event_bus.publish(
                request_id,
                Event(type="status", data={
                    "state": State.PREVIEW_READY.value,
                    "preview_url": instance.url,
                    "branch": branch,
                }),
            )
            # Plan 9 Task 3：append AI 消息到 conversation（如挂了的话），便于
            # 业务员在 ChatPanel 里看到「AI: 改完了 → 预览 {url}」一条
            cr_for_conv = self.repository.get(request_id)
            if cr_for_conv is not None and cr_for_conv.conversation_id:
                try:
                    from orchestrator.conversation import ConversationRepository
                    sha_str = ""
                    if isinstance(sha, dict):
                        sha_str = ", ".join(f"{r}:{s[:8]}" for r, s in sorted(sha.items()))
                    elif sha:
                        sha_str = sha[:8]
                    ConversationRepository(self.repository._db).append_message(
                        cr_for_conv.conversation_id,
                        {
                            "type": "ai",
                            "ts": _now_iso(),
                            "content": f"改完了 → 预览就绪 {instance.url}\n分支 {branch} commit {sha_str}",
                            "cr_id": request_id,
                        },
                    )
                except Exception:
                    pass  # 不阻塞主流程
            total = time.monotonic() - run_started
            await _phase_log("building", f"🏁 全流程完成 总耗时 {total:.1f}s")
            # 注意：到此 pipeline 结束，槽位仍占用，由 merge/discard/expire 释放
        except _PhaseError as pe:
            self.repository.mark_failed(
                request_id, phase=pe.phase, reason=pe.reason, log=pe.log
            )
            await self.event_bus.publish(
                request_id,
                Event(
                    type="status",
                    data={
                        "state": State.FAILED.value,
                        "phase": pe.phase,
                        "reason": pe.reason,
                    },
                ),
            )
            self.quota.release(request_id)
        except BaseException as exc:  # noqa: BLE001
            # 任何 _PhaseError 之外的异常（git 错误、DB 错误、CancelledError、
            # 编程 bug）都必须收敛 —— 否则 CR 卡死、配额泄漏、扩展端无任何提示。
            tb = "".join(traceback.format_exception(exc))
            try:
                current = self.repository.get(request_id).state
            except Exception:
                current = None
            phase_guess = {
                State.CLARIFYING: "clarifying",
                State.LOCATED: "coding",
                State.CODING: "coding",
                State.BUILDING: "building",
            }.get(current, "unknown")
            try:
                self.repository.mark_failed(
                    request_id, phase=phase_guess, reason="unhandled", log=tb,
                )
            except Exception:
                pass
            try:
                await self.event_bus.publish(
                    request_id,
                    Event(
                        type="status",
                        data={
                            "state": State.FAILED.value,
                            "phase": phase_guess,
                            "reason": "unhandled",
                        },
                    ),
                )
                await self.event_bus.publish_log(
                    request_id, phase_guess,
                    f"✗ 未捕获异常：{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            self.quota.release(request_id)
            if isinstance(exc, asyncio.CancelledError):
                raise
