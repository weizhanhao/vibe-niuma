"""确定性 fake adapter 实现 —— 让 Orchestrator 编排机器能在测试里跑通完整 FSM。

Plan 3 用真实实现替换它们；Orchestrator 主体不变。每个 fake 的「行为开关」让测试能精确
触发成功/失败/无产出等路径。
"""
import uuid

from orchestrator.adapters.interfaces import InteractionChannel
from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    LocateResult,
    PreviewInstance,
    RawRequest,
    RequestBrief,
    RunResult,
)


class FakeInteractionSkill:
    """按 question_count 走脚本化澄清；0 表示直接跳过。"""

    def __init__(self, question_count: int = 0):
        self._question_count = question_count

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief:
        clarifications: list[dict] = []
        for i in range(self._question_count):
            q = f"澄清问题 {i + 1}：你想要的业务效果是？"
            answer = await channel.ask(q, None)
            clarifications.append({"question": q, "answer": answer})
        return RequestBrief(
            original_text=raw.request_text, clarifications=clarifications
        )


class FakeStackAdapter:
    def __init__(self, locate_succeeds: bool = True, build_succeeds: bool = True):
        self._locate_succeeds = locate_succeeds
        self._build_succeeds = build_succeeds

    async def locate(self, url: str) -> LocateResult:
        if not self._locate_succeeds:
            return LocateResult(entry_files=[], route_path="")
        return LocateResult(
            entry_files=["src/pages/OrderList.tsx"], route_path="/orders"
        )

    async def context_pack(
        self, locate_result: LocateResult, raw: RawRequest, brief: RequestBrief
    ) -> DevContext:
        return DevContext(
            brief=brief,
            locate_result=locate_result,
            screenshot_b64=raw.screenshot_b64,
            box_coords=raw.box_coords,
        )

    async def build(
        self, repo_path: str, branch: str, *, log=None
    ) -> BuildResult:
        # Phase F：`log` 是 Pipeline 注入的 LogSink，fake 忽略。
        if not self._build_succeeds:
            return BuildResult(ok=False, log="fake build failure")
        return BuildResult(ok=True, log="fake build ok")


class FakeDevRunner:
    def __init__(self, produces_changes: bool = True, raises: bool = False):
        self._produces_changes = produces_changes
        self._raises = raises

    async def run(
        self, repo_path: str, branch: str, ctx: DevContext
    ) -> RunResult:
        if self._raises:
            raise RuntimeError("fake dev runner crash")
        if not self._produces_changes:
            return RunResult(changed=False, commit_sha=None, log="fake: no changes")
        return RunResult(
            changed=True, commit_sha=uuid.uuid4().hex[:12], log="fake: changed 1 file"
        )


class FakePreviewAdapter:
    def __init__(self, serve_succeeds: bool = True):
        self._serve_succeeds = serve_succeeds
        self.live_handles: set[str] = set()
        self._port = 5100
        # Plan 10 Task 7：handle → url 反查表，refine 路径复用同容器
        self._handle_to_url: dict[str, str] = {}

    async def serve(
        self, repo_path: str, branch: str, *,
        log=None, base_handle: str | None = None,
    ) -> PreviewInstance:
        # Phase F：`log` 是 Pipeline 注入的 LogSink，fake 忽略。
        if not self._serve_succeeds:
            raise RuntimeError("fake preview serve failure")
        # Plan 10 Task 7：base_handle 非空 → 复用同容器（refine 路径用）
        if base_handle and base_handle in self._handle_to_url:
            return PreviewInstance(
                preview_id=uuid.uuid4().hex[:12],
                url=self._handle_to_url[base_handle],
                handle=base_handle,
            )
        self._port += 1
        handle = f"fake-container-{uuid.uuid4().hex[:8]}"
        url = f"http://localhost:{self._port}"
        self.live_handles.add(handle)
        self._handle_to_url[handle] = url
        return PreviewInstance(
            preview_id=uuid.uuid4().hex[:12],
            url=url,
            handle=handle,
        )

    async def teardown(self, instance: PreviewInstance) -> None:
        self.live_handles.discard(instance.handle)
