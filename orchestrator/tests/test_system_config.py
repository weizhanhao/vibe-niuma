"""Plan 6 Task 1 —— system_config singleton + 乐观锁更新。

设计：
- `id` 永远 = 1，全表只一行。
- `version` 单调递增；`update(patch, expected_version)` 不匹配抛 `StaleVersionError`。
- 字段对应 Plan 6 schema 的 `server.*`（dev_runner / dev_model / vision_model
  / *_api_key / demo_repo_path / preview_backend_url）。
"""
import pytest

from orchestrator.system_config import (
    StaleVersionError,
    SystemConfigRepository,
)


@pytest.fixture
def cfg_repo(db_session):
    """每个 system_config 用例都从一张空表开始 —— 避免 db_session 跨用例共享 SystemConfig 行污染。"""
    from orchestrator.models import SystemConfig

    db_session.query(SystemConfig).delete()
    db_session.commit()
    return SystemConfigRepository(db_session)


def test_get_or_create_returns_singleton_with_defaults_on_first_call(cfg_repo, db_session):
    cfg = cfg_repo.get_or_create()

    assert cfg.id == 1
    assert cfg.version == 0
    assert cfg.dev_runner == "opencode"
    assert cfg.dev_model == "deepseek/deepseek-v4-flash"
    assert cfg.vision_model == "qwen-vl-plus"
    assert cfg.deepseek_api_key is None
    assert cfg.dashscope_api_key is None
    assert cfg.anthropic_api_key is None
    assert cfg.demo_repo_path == "/opt/vibe-niuma/demo"
    assert cfg.preview_backend_url == "http://vibe-niuma-demo-backend:8000"

    # 副作用：commit 进了 DB —— 用新 session 实例化的 repo 也能看到
    from orchestrator.models import SystemConfig
    assert db_session.get(SystemConfig, 1) is not None


def test_get_or_create_returns_existing_on_second_call(cfg_repo):
    first = cfg_repo.get_or_create()
    second = cfg_repo.get_or_create()

    assert first.id == second.id == 1
    # version 还是 0（没改过）；不会重复 INSERT
    assert second.version == 0


def test_update_with_correct_expected_version_succeeds_and_bumps(cfg_repo):
    cfg = cfg_repo.get_or_create()
    assert cfg.version == 0

    updated = cfg_repo.update(
        {"dev_model": "claude-sonnet-4", "deepseek_api_key": "sk-xxx"},
        expected_version=0,
    )

    assert updated.dev_model == "claude-sonnet-4"
    assert updated.deepseek_api_key == "sk-xxx"
    assert updated.version == 1


def test_update_with_stale_version_raises_StaleVersionError(cfg_repo):
    cfg_repo.get_or_create()  # version=0
    cfg_repo.update({"dev_model": "x"}, expected_version=0)  # → version=1

    # 第二次还拿 0 当 expected → 应该被打回
    with pytest.raises(StaleVersionError):
        cfg_repo.update({"dev_model": "y"}, expected_version=0)


def test_update_partial_preserves_other_fields(cfg_repo):
    cfg_repo.get_or_create()

    updated = cfg_repo.update(
        {"deepseek_api_key": "sk-only-this"},
        expected_version=0,
    )

    # 只动了 deepseek_api_key，其他默认值不变
    assert updated.deepseek_api_key == "sk-only-this"
    assert updated.dev_model == "deepseek/deepseek-v4-flash"
    assert updated.dev_runner == "opencode"
    assert updated.vision_model == "qwen-vl-plus"
    assert updated.dashscope_api_key is None
    assert updated.anthropic_api_key is None
    assert updated.demo_repo_path == "/opt/vibe-niuma/demo"
    assert updated.preview_backend_url == "http://vibe-niuma-demo-backend:8000"
    assert updated.version == 1
