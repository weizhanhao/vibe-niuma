"""Pipeline —— FSM 驱动器。把一条 `created` 的变更请求推过
clarify → locate → run → build/serve，每步写状态 + 推 SSE。

设计文档约束：
- 离开 `created` 前 acquire 配额槽位；进入失败终态时 release。
- 到 `preview-ready` 时 pipeline.run() 结束 —— 槽位仍被占用，由后续 merge/discard/expire 释放。
- 阻塞型 git 操作用 asyncio.to_thread 包装。
- coding 阶段只 create_branch，改代码+commit 委托给 dev_runner.run。
"""
import asyncio
import traceback

from orchestrator.adapters.interfaces import (
    DevRunnerAdapter,
    InteractionSkill,
    PreviewAdapter,
    StackAdapter,
)
from orchestrator.adapters.types import RawRequest
from orchestrator.events import Event, EventBus
from orchestrator.git_manager import GitManager
from orchestrator.interaction_channel import SSEInteractionChannel
from orchestrator.quota import QuotaManager
from orchestrator.repository import ChangeRequestRepository
from orchestrator.states import State


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
        self._channels: dict[str, SSEInteractionChannel] = {}

    def channel_for(self, request_id: str) -> SSEInteractionChannel:
        """暴露某请求的交互通道，供 REST /answer 端点回填答案。"""
        return self._channels[request_id]

    async def _set_state(self, request_id: str, state: State) -> None:
        self.repository.transition(request_id, state)
        await self.event_bus.publish(
            request_id, Event(type="status", data={"state": state.value})
        )

    async def run(self, request_id: str) -> None:
        """驱动一条 `created` 请求。异常路径统一收敛到 _PhaseError → mark_failed。"""
        await self.quota.acquire(request_id)
        try:
            # created → clarifying
            await self._set_state(request_id, State.CLARIFYING)
            channel = SSEInteractionChannel(request_id, self.event_bus)
            self._channels[request_id] = channel
            cr = self.repository.get(request_id)
            raw = RawRequest(
                url=cr.url,
                screenshot_b64=cr.screenshot_b64,
                box_coords=cr.box_coords,
                viewport=cr.viewport,
                request_text=cr.request_text,
            )
            brief = await self.interaction_skill.clarify(raw, channel)

            # clarifying → located
            locate_result = await self.stack_adapter.locate(raw.url)
            if not locate_result.entry_files:
                raise _PhaseError(
                    "located", "no-route-match", f"URL 未匹配任何路由: {raw.url}"
                )
            await self._set_state(request_id, State.LOCATED)

            # located → coding
            branch = f"cr/{request_id}"
            await asyncio.to_thread(self.git_manager.create_branch, branch)
            self.repository.set_branch(request_id, branch)
            await self._set_state(request_id, State.CODING)
            ctx = await self.stack_adapter.context_pack(locate_result, raw, brief)
            try:
                run_result = await self.dev_runner.run(self.repo_path, branch, ctx)
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "coding", "runner-error", "".join(traceback.format_exception(exc))
                ) from exc
            if not run_result.changed:
                raise _PhaseError("coding", "no-changes", run_result.log)

            # coding → building
            await self._set_state(request_id, State.BUILDING)
            build_result = await self.stack_adapter.build(self.repo_path, branch)
            if not build_result.ok:
                raise _PhaseError("building", "build-failed", build_result.log)
            try:
                instance = await self.preview_adapter.serve(self.repo_path, branch)
            except Exception as exc:  # noqa: BLE001
                raise _PhaseError(
                    "building", "container", "".join(traceback.format_exception(exc))
                ) from exc

            # building → preview-ready
            self.repository.set_preview(
                request_id, url=instance.url, handle=instance.handle
            )
            await self._set_state(request_id, State.PREVIEW_READY)
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
