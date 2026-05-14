import inspect

from orchestrator.adapters.interfaces import (
    DevRunnerAdapter,
    InteractionChannel,
    InteractionSkill,
    PreviewAdapter,
    StackAdapter,
)


def test_interaction_channel_methods_are_async():
    assert inspect.iscoroutinefunction(InteractionChannel.ask)
    assert inspect.iscoroutinefunction(InteractionChannel.present_variants)


def test_interaction_skill_clarify_is_async():
    assert inspect.iscoroutinefunction(InteractionSkill.clarify)


def test_stack_adapter_methods_are_async():
    assert inspect.iscoroutinefunction(StackAdapter.locate)
    assert inspect.iscoroutinefunction(StackAdapter.context_pack)
    assert inspect.iscoroutinefunction(StackAdapter.build)


def test_dev_runner_run_is_async():
    assert inspect.iscoroutinefunction(DevRunnerAdapter.run)


def test_preview_adapter_methods_are_async():
    assert inspect.iscoroutinefunction(PreviewAdapter.serve)
    assert inspect.iscoroutinefunction(PreviewAdapter.teardown)


def test_interfaces_are_runtime_checkable_protocols():
    # 一个满足结构的对象应被 isinstance 认可
    class _Stub:
        async def run(self, repo_path, branch, ctx):
            ...

    assert isinstance(_Stub(), DevRunnerAdapter)

    class _NotStub:
        pass

    assert not isinstance(_NotStub(), DevRunnerAdapter)
