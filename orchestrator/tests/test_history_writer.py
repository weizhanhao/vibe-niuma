"""history_writer 单元测试 —— Phase D。

覆盖：
- small CR 仅写 result.md
- large CR 写 result.md + spec.md + plan.md
- 分级边界（0/1/2/3 命中 → small/medium/large/large）
- 各客观信号单独触发命中
- 关键字大小写不敏感命中
- 同 CR 重写幂等覆盖
- 之前 large 后续降为 small：清理遗留 spec/plan
- pipeline 集成：preview-ready 后历史目录存在
- pipeline 集成：write_history 异常被吞掉，pipeline 仍正常完成
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.adapters.types import (
    HtmlMockup,
    LocateResult,
    RawRequest,
    RequestBrief,
)
from orchestrator.history_writer import (
    SizeClassification,
    _history_root,
    classify_size,
    write_history,
)


# ── helpers ─────────────────────────────────────────────────────────────


def _raw(text: str = "把保存按钮改成蓝色") -> RawRequest:
    return RawRequest(
        url="http://demo.local/orders",
        screenshot_b64="img-b64",
        box_coords={"x": 1, "y": 2},
        viewport={"width": 1280, "height": 800},
        request_text=text,
    )


def _brief(
    *,
    text: str = "把保存按钮改成蓝色",
    clarifications: list[dict] | None = None,
    selected_mockup: HtmlMockup | None = None,
) -> RequestBrief:
    return RequestBrief(
        original_text=text,
        clarifications=list(clarifications or []),
        selected_mockup=selected_mockup,
    )


def _locate(files: list[str] | None = None, route: str = "/orders") -> LocateResult:
    return LocateResult(
        entry_files=list(files or ["src/pages/Orders.tsx"]), route_path=route,
    )


# ── classify_size ───────────────────────────────────────────────────────


def test_classify_small_when_no_signals_hit():
    c = classify_size(
        brief=_brief(),
        raw_request=_raw("改颜色"),
        locate_result=_locate(),
        dev_runner_files_changed=["src/pages/Orders.tsx"],
    )
    assert isinstance(c, SizeClassification)
    assert c.size == "small"
    assert c.reasons == ()


def test_classify_medium_when_exactly_one_signal_hits():
    """单条命中 = medium。这里用「多次澄清」单点触发。"""
    c = classify_size(
        brief=_brief(
            clarifications=[
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
            ]
        ),
        raw_request=_raw("改颜色"),
        locate_result=_locate(["src/pages/Orders.tsx"]),
        dev_runner_files_changed=["src/pages/Orders.tsx"],
    )
    assert c.size == "medium"
    assert len(c.reasons) == 1
    assert c.reasons[0].startswith("多次澄清")


def test_classify_large_when_two_signals_hit():
    """澄清次数 > 1 + 入口文件 >= 3 → 命中 2 项 = large。"""
    c = classify_size(
        brief=_brief(
            clarifications=[
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
                {"question": "q3", "answer": "a3"},
            ]
        ),
        raw_request=_raw("改颜色"),
        locate_result=_locate(["a.tsx", "b.tsx", "c.tsx"]),
        dev_runner_files_changed=None,
    )
    assert c.size == "large"
    assert len(c.reasons) == 2


def test_classify_long_request_text_triggers_signal():
    """request_text > 80 字符单独命中 = medium。"""
    long_text = "改" * 100
    c = classify_size(
        brief=_brief(text=long_text),
        raw_request=_raw(long_text),
        locate_result=_locate(),
        dev_runner_files_changed=None,
    )
    assert c.size == "medium"
    assert any("长需求文本" in r for r in c.reasons)


def test_classify_keyword_intent_case_insensitive():
    c = classify_size(
        brief=_brief(text="想做一次 Large-Change 的拆分"),
        raw_request=_raw("想做一次 Large-Change 的拆分"),
        locate_result=_locate(),
        dev_runner_files_changed=None,
    )
    assert any("意图关键字" in r for r in c.reasons)


def test_classify_keyword_intent_chinese():
    c = classify_size(
        brief=_brief(),
        raw_request=_raw("这是个重构需求"),
        locate_result=_locate(),
        dev_runner_files_changed=None,
    )
    assert any("意图关键字" in r for r in c.reasons)


def test_classify_files_changed_none_does_not_count():
    c = classify_size(
        brief=_brief(),
        raw_request=_raw("短文本"),
        locate_result=_locate(),
        dev_runner_files_changed=None,
    )
    assert c.size == "small"


def test_classify_files_changed_over_threshold_counts():
    c = classify_size(
        brief=_brief(),
        raw_request=_raw("短"),
        locate_result=_locate(),
        dev_runner_files_changed=["a", "b", "c", "d", "e"],
    )
    assert any("多文件改动" in r for r in c.reasons)


def test_classify_entry_files_threshold_inclusive():
    """入口文件「≥ 3」是包含边界 —— 正好 3 个就该命中。"""
    c_three = classify_size(
        brief=_brief(),
        raw_request=_raw("短"),
        locate_result=_locate(["a", "b", "c"]),
        dev_runner_files_changed=None,
    )
    assert any("多文件入口" in r for r in c_three.reasons)

    c_two = classify_size(
        brief=_brief(),
        raw_request=_raw("短"),
        locate_result=_locate(["a", "b"]),
        dev_runner_files_changed=None,
    )
    assert not any("多文件入口" in r for r in c_two.reasons)


def test_classify_clarify_threshold_exclusive():
    """澄清次数「> 1」是排他边界 —— 正好 1 个不该命中。"""
    c_one = classify_size(
        brief=_brief(clarifications=[{"question": "q", "answer": "a"}]),
        raw_request=_raw("短"),
        locate_result=_locate(),
        dev_runner_files_changed=None,
    )
    assert not any("多次澄清" in r for r in c_one.reasons)

    c_two = classify_size(
        brief=_brief(
            clarifications=[
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
            ]
        ),
        raw_request=_raw("短"),
        locate_result=_locate(),
        dev_runner_files_changed=None,
    )
    assert any("多次澄清" in r for r in c_two.reasons)


# ── write_history 输出 ──────────────────────────────────────────────────


def test_small_writes_only_result(tmp_path: Path):
    write_history(
        repo_path=str(tmp_path),
        request_id="cr-small-1",
        raw_request=_raw("改颜色"),
        brief=_brief(),
        locate_result=_locate(),
        branch="cr/cr-small-1",
        preview_url="http://localhost:5101",
        phase_timings={
            "clarifying": 1.0,
            "locating": 0.2,
            "coding": 5.0,
            "building": 3.0,
        },
        total_elapsed=9.2,
        dev_runner_files_changed=["src/pages/Orders.tsx"],
    )
    out_dir = tmp_path / ".vibe-niuma" / "history" / "cr-cr-small-1"
    assert (out_dir / "result.md").exists()
    assert not (out_dir / "spec.md").exists()
    assert not (out_dir / "plan.md").exists()
    text = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "**small**" in text
    assert "http://localhost:5101" in text
    assert "cr/cr-small-1" in text


def test_large_writes_all_three(tmp_path: Path):
    write_history(
        repo_path=str(tmp_path),
        request_id="cr-large-1",
        raw_request=_raw("重构订单列表"),
        brief=_brief(
            text="重构订单列表",
            clarifications=[
                {"question": "想保留分页吗？", "answer": "要"},
                {"question": "排序保留吗？", "answer": "保留默认"},
            ],
        ),
        locate_result=_locate(
            ["src/pages/Orders.tsx", "src/components/OrderRow.tsx", "src/api/order.ts"]
        ),
        branch="cr/cr-large-1",
        preview_url="http://localhost:5102",
        phase_timings={
            "clarifying": 4.2,
            "locating": 0.3,
            "coding": 30.0,
            "building": 12.1,
        },
        total_elapsed=46.6,
        dev_runner_files_changed=["a.tsx", "b.tsx", "c.tsx", "d.tsx", "e.tsx"],
    )
    out_dir = tmp_path / ".vibe-niuma" / "history" / "cr-cr-large-1"
    assert (out_dir / "result.md").exists()
    assert (out_dir / "spec.md").exists()
    assert (out_dir / "plan.md").exists()

    spec = (out_dir / "spec.md").read_text(encoding="utf-8")
    assert "多次澄清" in spec
    assert "意图关键字" in spec
    assert "多文件入口" in spec
    assert "多文件改动" in spec
    assert "想保留分页吗？" in spec

    plan = (out_dir / "plan.md").read_text(encoding="utf-8")
    assert "## clarifying" in plan
    assert "## locating" in plan
    assert "## coding" in plan
    assert "## building" in plan
    assert "cr/cr-large-1" in plan


def test_idempotent_rewrite_overwrites_cleanly(tmp_path: Path):
    common = dict(
        repo_path=str(tmp_path),
        request_id="cr-rerun",
        raw_request=_raw("改颜色"),
        brief=_brief(),
        locate_result=_locate(),
        branch="cr/cr-rerun",
        preview_url="http://localhost:5103",
        phase_timings={"clarifying": 1.0},
        total_elapsed=1.0,
        dev_runner_files_changed=["a.tsx"],
    )
    write_history(**common)
    out_file = tmp_path / ".vibe-niuma" / "history" / "cr-cr-rerun" / "result.md"
    first = out_file.read_text(encoding="utf-8")

    common2 = {**common, "preview_url": "http://localhost:5999"}
    write_history(**common2)
    second = out_file.read_text(encoding="utf-8")
    assert "http://localhost:5999" in second
    assert "http://localhost:5103" not in second
    assert first != second


def test_demote_large_to_small_removes_stale_spec_plan(tmp_path: Path):
    """先写一个 large（生成 spec+plan），再用同 id 写 small —— spec/plan 应被清掉。"""
    large_args = dict(
        repo_path=str(tmp_path),
        request_id="cr-demote",
        raw_request=_raw("重构订单页"),
        brief=_brief(
            text="重构订单页",
            clarifications=[
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
            ],
        ),
        locate_result=_locate(["a.tsx", "b.tsx", "c.tsx"]),
        branch="cr/cr-demote",
        preview_url="http://localhost:5104",
        phase_timings={"clarifying": 2.0, "coding": 5.0},
        total_elapsed=7.0,
        dev_runner_files_changed=None,
    )
    write_history(**large_args)
    out_dir = tmp_path / ".vibe-niuma" / "history" / "cr-cr-demote"
    assert (out_dir / "spec.md").exists()
    assert (out_dir / "plan.md").exists()

    small_args = {
        **large_args,
        "raw_request": _raw("改颜色"),
        "brief": _brief(),
        "locate_result": _locate(["a.tsx"]),
    }
    write_history(**small_args)
    assert (out_dir / "result.md").exists()
    assert not (out_dir / "spec.md").exists()
    assert not (out_dir / "plan.md").exists()


def test_result_includes_clarify_qa_when_present(tmp_path: Path):
    write_history(
        repo_path=str(tmp_path),
        request_id="cr-qa",
        raw_request=_raw("加搜索"),
        brief=_brief(
            clarifications=[{"question": "按啥搜？", "answer": "客户名"}]
        ),
        locate_result=_locate(),
        branch="cr/cr-qa",
        preview_url=None,
        phase_timings={"clarifying": 1.0},
        total_elapsed=1.0,
        dev_runner_files_changed=None,
    )
    text = (
        tmp_path / ".vibe-niuma" / "history" / "cr-cr-qa" / "result.md"
    ).read_text(encoding="utf-8")
    assert "按啥搜？" in text
    assert "客户名" in text


def test_result_handles_missing_preview_url(tmp_path: Path):
    write_history(
        repo_path=str(tmp_path),
        request_id="cr-no-preview",
        raw_request=_raw("改颜色"),
        brief=_brief(),
        locate_result=_locate(),
        branch="cr/cr-no-preview",
        preview_url=None,
        phase_timings={},
        total_elapsed=0.0,
        dev_runner_files_changed=None,
    )
    text = (
        tmp_path / ".vibe-niuma" / "history" / "cr-cr-no-preview" / "result.md"
    ).read_text(encoding="utf-8")
    assert "（未生成）" in text or "(未生成)" in text


def test_result_records_selected_mockup_when_present(tmp_path: Path):
    mockup = HtmlMockup(id="v1", title="方向 A", html="<div/>")
    write_history(
        repo_path=str(tmp_path),
        request_id="cr-mockup",
        raw_request=_raw("重做首页"),
        brief=_brief(text="重做首页", selected_mockup=mockup),
        locate_result=_locate(),
        branch="cr/cr-mockup",
        preview_url="http://x",
        phase_timings={"clarifying": 1.0},
        total_elapsed=1.0,
        dev_runner_files_changed=None,
    )
    text = (
        tmp_path / ".vibe-niuma" / "history" / "cr-cr-mockup" / "result.md"
    ).read_text(encoding="utf-8")
    assert "v1" in text
    assert "方向 A" in text


# ── pipeline 集成 ──────────────────────────────────────────────────────


@pytest.fixture
def temp_repo(tmp_path):
    """带 main 分支 + 一个初始 commit 的 git 仓库。"""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(
        ["git", *a], cwd=repo, check=True, capture_output=True,
    )
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "file.txt").write_text("v1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return repo


async def test_pipeline_writes_history_on_preview_ready(temp_repo, db_session):
    """走一遍 fake pipeline，preview-ready 后历史目录应存在 result.md。"""
    from orchestrator.adapters.fakes import (
        FakeDevRunner,
        FakeInteractionSkill,
        FakePreviewAdapter,
        FakeStackAdapter,
    )
    from orchestrator.events import EventBus
    from orchestrator.git_manager import GitManager
    from orchestrator.pipeline import Pipeline
    from orchestrator.quota import QuotaManager
    from orchestrator.repository import ChangeRequestRepository
    from orchestrator.states import State

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(
        RawRequest(
            url="http://demo/orders",
            screenshot_b64="img",
            box_coords={},
            viewport={},
            request_text="把保存按钮改蓝",
        )
    )
    pipeline = Pipeline(
        repo_path=str(temp_repo),
        repository=repo,
        git_manager=GitManager(str(temp_repo)),
        event_bus=EventBus(),
        quota=QuotaManager(capacity=5),
        interaction_skill=FakeInteractionSkill(question_count=0),
        stack_adapter=FakeStackAdapter(),
        dev_runner=FakeDevRunner(),
        preview_adapter=FakePreviewAdapter(),
    )
    await pipeline.run(cr.id)
    assert repo.get(cr.id).state == State.PREVIEW_READY.value

    out_dir = temp_repo / ".vibe-niuma" / "history" / f"cr-{cr.id}"
    assert (out_dir / "result.md").exists(), "preview-ready 后应写 result.md"
    text = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "**small**" in text


async def test_pipeline_history_write_failure_does_not_break_pipeline(
    temp_repo, db_session, monkeypatch
):
    """write_history 抛异常时 pipeline 仍能完成到 preview-ready。"""
    from orchestrator.adapters.fakes import (
        FakeDevRunner,
        FakeInteractionSkill,
        FakePreviewAdapter,
        FakeStackAdapter,
    )
    from orchestrator import pipeline as pipeline_module
    from orchestrator.events import EventBus
    from orchestrator.git_manager import GitManager
    from orchestrator.pipeline import Pipeline
    from orchestrator.quota import QuotaManager
    from orchestrator.repository import ChangeRequestRepository
    from orchestrator.states import State

    def _boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline_module, "write_history", _boom)

    repo = ChangeRequestRepository(db_session)
    cr = repo.create(
        RawRequest(
            url="http://demo/orders",
            screenshot_b64="img",
            box_coords={},
            viewport={},
            request_text="改颜色",
        )
    )
    pipeline = Pipeline(
        repo_path=str(temp_repo),
        repository=repo,
        git_manager=GitManager(str(temp_repo)),
        event_bus=EventBus(),
        quota=QuotaManager(capacity=5),
        interaction_skill=FakeInteractionSkill(question_count=0),
        stack_adapter=FakeStackAdapter(),
        dev_runner=FakeDevRunner(),
        preview_adapter=FakePreviewAdapter(),
    )
    await pipeline.run(cr.id)
    fetched = repo.get(cr.id)
    assert fetched.state == State.PREVIEW_READY.value, (
        "history 写盘异常不应让 pipeline 状态偏离 preview-ready"
    )


# ── 多仓项目路径语义 ─────────────────────────────────────────────────────


def _make_sub_repo(parent: Path, name: str) -> Path:
    """在 parent/<name>/ 下创建一个最简 git 仓库，返回其路径。"""
    import subprocess

    sub = parent / name
    sub.mkdir()
    run = lambda *a: subprocess.run(
        ["git", *a], cwd=sub, check=True, capture_output=True,
    )
    run("init", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (sub / "file.txt").write_text("v1\n")
    run("add", "file.txt")
    run("commit", "-m", "init")
    return sub


def test_history_root_helper_returns_project_root_for_multi_repo(tmp_path: Path):
    """_history_root() 在多子仓项目下应返回 project_root/.vibe-niuma/history。"""
    # Arrange: 项目根含两个子仓（各自有 .git）
    _make_sub_repo(tmp_path, "frontend")
    _make_sub_repo(tmp_path, "backend")

    # Act
    result = _history_root(str(tmp_path))

    # Assert: 落在项目根（tmp_path），不在任一子仓内
    assert result == tmp_path / ".vibe-niuma" / "history"


def test_history_root_helper_unchanged_for_single_repo(tmp_path: Path):
    """_history_root() 在单仓（无子目录含 .git）下保持原有行为。"""
    # Arrange: 只是一个普通目录，没有含 .git 的子目录
    (tmp_path / "src").mkdir()

    # Act
    result = _history_root(str(tmp_path))

    # Assert: 同样落在传入路径的 .vibe-niuma/history
    assert result == tmp_path / ".vibe-niuma" / "history"


def test_history_writes_to_project_root_in_multi_repo(tmp_path: Path):
    """多仓项目下 write_history 必须写到 project_root/.vibe-niuma/history/，
    不能写到任何子仓内部。"""
    # Arrange: 建 frontend/ + backend/ 两个子仓
    _make_sub_repo(tmp_path, "frontend")
    _make_sub_repo(tmp_path, "backend")

    # Act
    write_history(
        repo_path=str(tmp_path),
        request_id="multi-cr-1",
        raw_request=_raw("加搜索功能"),
        brief=_brief(),
        locate_result=_locate(["frontend/src/App.tsx", "backend/src/api.py"]),
        branch="cr/multi-cr-1",
        preview_url="http://localhost:5200",
        phase_timings={"clarifying": 1.0, "coding": 5.0},
        total_elapsed=6.0,
        dev_runner_files_changed=["frontend/src/App.tsx", "backend/src/api.py"],
    )

    # Assert 1: 历史目录在项目根
    project_history_dir = tmp_path / ".vibe-niuma" / "history" / "cr-multi-cr-1"
    assert project_history_dir.exists(), (
        f"多仓项目的历史应写到 {project_history_dir}，但目录不存在"
    )
    assert (project_history_dir / "result.md").exists()

    # Assert 2: 子仓内部不应有 .vibe-niuma/
    assert not (tmp_path / "frontend" / ".vibe-niuma").exists(), (
        "frontend 子仓内部不应出现 .vibe-niuma/ 目录"
    )
    assert not (tmp_path / "backend" / ".vibe-niuma").exists(), (
        "backend 子仓内部不应出现 .vibe-niuma/ 目录"
    )


def test_history_path_unchanged_in_single_repo(tmp_path: Path):
    """单仓项目下 write_history 行为与原先完全一致：历史写到 repo/.vibe-niuma/history/。"""
    # Arrange: 普通单仓，没有含 .git 的子目录
    (tmp_path / "src").mkdir()

    # Act
    write_history(
        repo_path=str(tmp_path),
        request_id="single-cr-1",
        raw_request=_raw("改颜色"),
        brief=_brief(),
        locate_result=_locate(),
        branch="cr/single-cr-1",
        preview_url=None,
        phase_timings={"coding": 2.0},
        total_elapsed=2.0,
        dev_runner_files_changed=["src/App.tsx"],
    )

    # Assert: 历史在 tmp_path（repo 根）
    out_dir = tmp_path / ".vibe-niuma" / "history" / "cr-single-cr-1"
    assert out_dir.exists()
    assert (out_dir / "result.md").exists()
    text = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "**small**" in text
