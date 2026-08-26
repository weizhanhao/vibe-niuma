import shutil
from pathlib import Path

import pytest

from vplatform.orchestration.dag import (
    PipelineError, default_pipeline, load_pipeline,
)
from vplatform.skills.installer import (
    discover, effective_skills, install_platform_skills, install_project_skills,
    verify_pipeline_skills,
)

REPO = Path(__file__).resolve().parents[2]


# ── DAG ──────────────────────────────────────────────────────────
def test_default_pipeline_shape():
    p = default_pipeline()
    assert [s.key for s in p.stages][:3] == ["triage", "clarify", "decompose"]
    assert [s.key for s in p.human_gates] == ["review", "release"]
    assert p.is_before("implement", "review")
    assert p.next_of("release") is None


def test_stage_reads_skill_and_adapter():
    p = default_pipeline()
    assert p.get("decompose").skill == "to-tickets"
    assert p.get("decompose").critic == "decompose-critic"
    assert p.get("ai_review").adapter == "ocr"
    assert p.get("ai_review").plus_skill == "code-review"      # 规格轴
    assert p.get("ai_review").block_on == ("critical",)
    assert p.get("implement").needs_workspace is True
    assert p.get("implement").parallel_over == "tasks"
    assert p.get("verify").commands == ["lint", "test", "build"]
    assert p.get("verify").on_failure["skill"] == "diagnosing-bugs"
    assert p.get("review").is_human_gate and p.get("review").approvers == 1


def test_required_skills_includes_nested_ones():
    """conflict 链和 on_failure 里的 skill 也要算进来，否则部署自检会漏。"""
    req = default_pipeline().required_skills
    assert "resolving-merge-conflicts" in req      # 藏在 conflict: [...] 里
    assert "diagnosing-bugs" in req                # 藏在 on_failure 里
    assert "decompose-critic" in req


def test_adding_a_stage_is_config_only():
    """加环节只改 YAML —— 这就是 D8 的验收标准。"""
    p = load_pipeline("""
pipeline:
  - clarify: {skill: grilling}
  - security_scan: {adapter: trivy, block_on: [critical]}
  - review: {gate: human}
""")
    assert [s.key for s in p.stages] == ["clarify", "security_scan", "review"]
    assert p.get("security_scan").adapter == "trivy"


@pytest.mark.parametrize("bad,msg", [
    ("stages: []", "pipeline"),
    ("pipeline: {}", "必须是列表"),
    ("pipeline:\n  - {a: {}, b: {}}", "单键映射"),
    ("pipeline:\n  - clarify: [1,2]", "必须是映射"),
    ("pipeline:\n  - a: {}\n  - a: {}", "重复"),
    ("pipeline: [", "YAML 解析失败"),
])
def test_bad_config_fails_at_load_not_at_runtime(bad, msg):
    """配置错要在加载期炸，不能等跑到那一步 —— 那时已经开了工位烧了 token。"""
    with pytest.raises(PipelineError, match=msg):
        load_pipeline(bad)


# ── Skill 三层 ───────────────────────────────────────────────────
@pytest.fixture()
def dist():
    d = REPO / "platform-skills" / "dist"
    if not d.is_dir():
        pytest.skip("platform-skills/dist 未构建（跑 platform-skills/build.sh）")
    return d


def test_vendored_skills_cover_the_pipeline(dist, tmp_path):
    """**端到端自检**：默认流水线要的 skill，vendored 的这批能不能装齐。"""
    home = tmp_path / "home"
    install_platform_skills(dist, home)
    missing = verify_pipeline_skills(default_pipeline(), home=home,
                                     worktree=tmp_path / "ws")
    assert missing == [], f"流水线要但装不到：{missing}"


def test_repo_skills_override_platform_defaults(dist, tmp_path):
    """L3 > L2 > L1：客户仓库自己的规范覆盖平台默认。"""
    home, ws = tmp_path / "home", tmp_path / "ws"
    install_platform_skills(dist, home)
    install_project_skills({"tdd": "---\nname: tdd\ndescription: 空间版\n---\n空间口径"}, ws)

    repo_skill = ws / ".claude" / "skills" / "tdd"
    repo_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: 仓库版\n---\n本仓口径", encoding="utf-8")

    eff = effective_skills(home=home, worktree=ws)
    assert eff["tdd"].layer == "L3"                     # 仓库自带赢
    assert "本仓口径" in (eff["tdd"].path / "SKILL.md").read_text(encoding="utf-8")
    # 没被覆盖的仍来自平台层
    assert eff["triage"].layer == "L1"


def test_project_layer_beats_platform(dist, tmp_path):
    home, ws = tmp_path / "home", tmp_path / "ws"
    install_platform_skills(dist, home)
    install_project_skills({"tdd": "---\nname: tdd\ndescription: x\n---\n空间口径"}, ws)
    assert effective_skills(home=home, worktree=ws)["tdd"].layer == "L2"


def test_platform_skills_do_not_pollute_customer_repo(dist, tmp_path):
    """L1 装在容器 HOME，不进 worktree —— 客户 clone 下来看不到我们塞的东西。"""
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    install_platform_skills(dist, home)
    assert not (ws / ".opencode").exists()
    assert not (ws / ".claude").exists()


def test_missing_skill_is_reported_before_running(tmp_path):
    """跑之前就要查出缺失。等 agent 调用时才发现，它会自己瞎编一套做法。"""
    p = load_pipeline("pipeline:\n  - x: {skill: nonexistent-skill}")
    missing = verify_pipeline_skills(p, home=tmp_path / "h", worktree=tmp_path / "w")
    assert missing == ["nonexistent-skill"]


def test_discover_reads_name_from_frontmatter(tmp_path):
    """目录名和 frontmatter 的 name 不一致时以 name 为准 —— opencode 认的是 name。"""
    d = tmp_path / "weird-dir-name"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: real-name\ndescription: d\n---\nbody",
                                encoding="utf-8")
    assert set(discover(tmp_path)) == {"real-name"}


# ── 浏览器自检（ego-browser）────────────────────────────────────
def test_browser_check_is_in_the_default_pipeline():
    """`verify` 跑的是 lint/test/build —— 那些全过，页面照样可能白屏、
    按钮点不动、接口 404。浏览器自检补的是「像个人一样真去点」。"""
    from vplatform.orchestration.dag import default_pipeline

    p = default_pipeline()
    keys = [s.key for s in p.stages]
    assert "browser_check" in keys
    # 必须在预览之后、人工审核之前 —— 预览没起来没得点，
    # 而它的意义就是让人工审核之前先自动点一遍
    assert keys.index("preview") < keys.index("browser_check") < keys.index("review")
    assert p.get("browser_check").label == "浏览器自检"
    assert "ego-browser" in p.required_skills


def test_the_ego_browser_skill_is_shipped():
    """skill 要真的在 dist 里，否则容器里的 agent 根本没有它。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "platform-skills"
    skill = root / "dist" / "ego-browser" / "SKILL.md"
    assert skill.exists(), "build.sh 没把 ego-browser 打进 dist"
    assert "ego-browser nodejs" in skill.read_text(encoding="utf-8")


def test_browser_check_degrades_honestly_when_not_installed(monkeypatch, tmp_path):
    """**没装就说没装。**

    ego lite 只有 macOS 版，而且是宿主上的桌面应用 —— 容器里的 agent
    够不着。探不到时必须说「跳过」，不能把「没检查」说成「通过」。
    """
    from pathlib import Path

    from vplatform.orchestration.stages import browser_available

    monkeypatch.setattr("shutil.which", lambda _b: None)
    # 本机真装了的话 ~/.local/bin 兜底会找到 —— HOME 也要指空
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "empty"))
    assert browser_available() is False
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/local/bin/ego-browser")
    assert browser_available() is True
