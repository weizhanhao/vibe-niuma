import pytest

from orchestrator.adapters.fakes import (
    FakeDevRunner,
    FakeInteractionSkill,
    FakePreviewAdapter,
    FakeStackAdapter,
)
from orchestrator.adapters.types import DevContext, LocateResult, RawRequest, RequestBrief


def _raw() -> RawRequest:
    return RawRequest(
        url="http://x/orders",
        screenshot_b64="img",
        box_coords={},
        viewport={},
        request_text="把保存按钮改成蓝色",
    )


class _RecordingChannel:
    """记录 ask/present_variants 调用的测试用 channel；按预设答案回复。"""

    def __init__(self, answers: list[str], selection_id: str | None = None):
        self._answers = list(answers)
        self._selection_id = selection_id
        self.asked: list[str] = []

    async def ask(self, question, options):
        self.asked.append(question)
        return self._answers.pop(0)

    async def present_variants(self, variants):
        from orchestrator.adapters.types import VariantSelection

        return VariantSelection(selected_id=self._selection_id)


async def test_fake_interaction_skill_skip_path():
    skill = FakeInteractionSkill(question_count=0)
    channel = _RecordingChannel(answers=[])
    brief = await skill.clarify(_raw(), channel)
    assert isinstance(brief, RequestBrief)
    assert brief.original_text == "把保存按钮改成蓝色"
    assert channel.asked == []


async def test_fake_interaction_skill_scripted_questions():
    skill = FakeInteractionSkill(question_count=2)
    channel = _RecordingChannel(answers=["答案1", "答案2"])
    brief = await skill.clarify(_raw(), channel)
    assert len(channel.asked) == 2
    assert len(brief.clarifications) == 2
    assert brief.clarifications[0]["answer"] == "答案1"


async def test_fake_stack_adapter_locate_hit_and_miss():
    adapter = FakeStackAdapter()
    hit = await adapter.locate("http://x/orders")
    assert hit.entry_files  # 默认命中
    miss_adapter = FakeStackAdapter(locate_succeeds=False)
    miss = await miss_adapter.locate("http://x/orders")
    assert miss.entry_files == []


async def test_fake_stack_adapter_build_outcome_configurable():
    assert (await FakeStackAdapter().build("/repo", "cr/1")).ok is True
    assert (await FakeStackAdapter(build_succeeds=False).build("/repo", "cr/1")).ok is False


async def test_fake_dev_runner_outcomes():
    ctx = DevContext(
        brief=RequestBrief(original_text="x"),
        locate_result=LocateResult(entry_files=["a"], route_path="/a"),
        screenshot_b64="img",
        box_coords={},
    )
    ok = await FakeDevRunner().run("/repo", "cr/1", ctx)
    assert ok.changed is True and ok.commit_sha is not None
    no_change = await FakeDevRunner(produces_changes=False).run("/repo", "cr/1", ctx)
    assert no_change.changed is False
    with pytest.raises(RuntimeError):
        await FakeDevRunner(raises=True).run("/repo", "cr/1", ctx)


async def test_fake_preview_adapter_serve_and_teardown():
    adapter = FakePreviewAdapter()
    inst = await adapter.serve("/repo", "cr/1")
    assert inst.url.startswith("http://")
    assert inst.handle in adapter.live_handles
    await adapter.teardown(inst)
    assert inst.handle not in adapter.live_handles
    with pytest.raises(RuntimeError):
        await FakePreviewAdapter(serve_succeeds=False).serve("/repo", "cr/1")
