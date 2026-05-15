"""Plan 3 接线契约：AppState.build_pipeline 默认使用真实 adapter，且 dev_runner
按 settings.dev_runner 选择 ClaudeCodeDevRunner 或 OpenCodeDevRunner。
"""
from unittest.mock import MagicMock

from orchestrator.adapters.impl.brainstorming_skill import BrainstormingSkill
from orchestrator.adapters.impl.claude_code_runner import ClaudeCodeDevRunner
from orchestrator.adapters.impl.docker_preview import DockerPreviewAdapter
from orchestrator.adapters.impl.opencode_runner import OpenCodeDevRunner
from orchestrator.adapters.impl.react_vite_stack import ReactViteStackAdapter


def test_build_pipeline_uses_real_adapters_by_default():
    from orchestrator.main import AppState

    state = AppState()
    pipeline = state.build_pipeline(MagicMock())

    assert isinstance(pipeline.interaction_skill, BrainstormingSkill)
    assert isinstance(pipeline.stack_adapter, ReactViteStackAdapter)
    assert isinstance(pipeline.preview_adapter, DockerPreviewAdapter)
    assert isinstance(pipeline.dev_runner, (ClaudeCodeDevRunner, OpenCodeDevRunner))


def test_build_pipeline_picks_dev_runner_by_config(monkeypatch):
    from orchestrator import main as main_mod
    from orchestrator.main import AppState

    monkeypatch.setattr(main_mod.settings, "dev_runner", "opencode")
    state = AppState()
    p = state.build_pipeline(MagicMock())
    assert isinstance(p.dev_runner, OpenCodeDevRunner)

    monkeypatch.setattr(main_mod.settings, "dev_runner", "claude-code")
    state = AppState()
    p = state.build_pipeline(MagicMock())
    assert isinstance(p.dev_runner, ClaudeCodeDevRunner)


def test_pipeline_factory_can_be_overridden_for_tests():
    from orchestrator.main import AppState

    state = AppState()
    sentinel = object()
    state.pipeline_factory = lambda db: sentinel
    assert state.build_pipeline(MagicMock()) is sentinel
