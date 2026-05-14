"""4 个 adapter 的 Protocol 接口 —— 跨计划契约。所有方法 async。

Plan 2 提供 fake 实现（fakes.py），Plan 3 提供真实实现。Orchestrator 主体只依赖这些接口。
"""
from typing import Protocol, runtime_checkable

from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    HtmlMockup,
    LocateResult,
    PreviewInstance,
    RawRequest,
    RequestBrief,
    RunResult,
    VariantSelection,
)


@runtime_checkable
class InteractionChannel(Protocol):
    """adapter 用它来「问业务员」；实现负责把问题经 SSE 推出去、等 /answer 端点回填。"""

    async def ask(self, question: str, options: list[str] | None) -> str: ...

    async def present_variants(
        self, variants: list[HtmlMockup]
    ) -> VariantSelection: ...


@runtime_checkable
class InteractionSkill(Protocol):
    """交互层：只问业务、不碰技术，产出业务级 RequestBrief。"""

    async def clarify(
        self, raw: RawRequest, channel: InteractionChannel
    ) -> RequestBrief: ...


@runtime_checkable
class StackAdapter(Protocol):
    """栈层：URL→源码定位、上下文组装、构建。"""

    async def locate(self, url: str) -> LocateResult: ...

    async def context_pack(
        self, locate_result: LocateResult, raw: RawRequest, brief: RequestBrief
    ) -> DevContext: ...

    async def build(self, repo_path: str, branch: str) -> BuildResult: ...


@runtime_checkable
class DevRunnerAdapter(Protocol):
    """开发层：业务 brief → 真实代码改动并 commit。"""

    async def run(
        self, repo_path: str, branch: str, ctx: DevContext
    ) -> RunResult: ...


@runtime_checkable
class PreviewAdapter(Protocol):
    """预览层：分支 → 隔离预览环境。"""

    async def serve(self, repo_path: str, branch: str) -> PreviewInstance: ...

    async def teardown(self, instance: PreviewInstance) -> None: ...
