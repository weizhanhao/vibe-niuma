"""ticket 解析测试 —— 夹具是 agent 在真实仓库上的真实产出。"""
from pathlib import Path

import pytest

from vplatform.orchestration.stages import parse_ticket, parse_tickets, topo_layers

FIXTURES = Path(__file__).parent / "fixtures"

STRICT = """# 02: 导出任务异步化

**What to build:** 导出改成后台任务，前端轮询进度

**Blocked by:** 01

**Repos:** merchant-api

**Touches:**
- `app/routers/export.py`
- `app/tasks/export_job.py`

**Contracts:** `POST /export/orders → {jobId}`

**Sequence:** migrate

**Status:** ready-for-agent
"""


def test_strict_template_is_fully_parsed():
    tk = parse_ticket(STRICT)
    assert tk.key == "T2" and tk.title == "导出任务异步化"
    assert tk.blocked_by == ["T1"]
    assert tk.repos == ["merchant-api"]
    assert set(tk.touches) == {"app/routers/export.py", "app/tasks/export_job.py"}
    assert tk.contracts == ["POST /export/orders → {jobId}"]
    assert tk.sequence == "migrate"


def test_real_agent_output_is_not_dropped():
    """**这份夹具是 agent 在 doBuyRight 上的真实产出。**

    它没照 to-tickets 的模板走（写成了自由格式的方案文档），
    第一版解析器直接判「未产出 ticket」，一份质量很高的拆解就这么丢了。
    解析器太脆比解析器太松更糟 —— 后者至少还能人工修。
    """
    text = (FIXTURES / "real_agent_ticket.md").read_text(encoding="utf-8")
    tk = parse_ticket(text)

    assert tk.title, "认不出标题"
    assert "胜率" in tk.title
    # 从 markdown 表格和正文的行内代码里捞出真实路径
    assert "backend/engine/strategy_metrics.py" in tk.touches
    assert "tests/test_strategy_metrics.py" in tk.touches
    assert "frontend/rules.html" in tk.touches
    # 不该把非路径的行内代码当成文件
    assert not any(t.startswith("_win_rate") for t in tk.touches)


def test_parse_tickets_reads_a_directory(tmp_path):
    d = tmp_path / "issues"
    d.mkdir()
    (d / "01-a.md").write_text("# 01: 第一个\n\n**Touches:**\n- `a/x.py`\n", encoding="utf-8")
    (d / "02-b.md").write_text("# 02: 第二个\n\n**Blocked by:** 01\n", encoding="utf-8")
    ts = parse_tickets(d)
    assert [t.key for t in ts] == ["T1", "T2"]
    assert ts[1].blocked_by == ["T1"]


def test_missing_dir_returns_empty(tmp_path):
    assert parse_tickets(tmp_path / "nope") == []


def test_unparseable_file_is_skipped_not_crashing(tmp_path):
    d = tmp_path / "issues"; d.mkdir()
    (d / "01-bad.md").write_text("既没有标题也没有字段\n只有一行", encoding="utf-8")
    (d / "02-ok.md").write_text("# 02: 好的\n", encoding="utf-8")
    ts = parse_tickets(d)
    assert [t.title for t in ts] == ["好的"]


# ── 拓扑分层 ─────────────────────────────────────────────────────
class _T:
    def __init__(self, key, deps=()):
        self.key = key; self.depends_on = list(deps)


def test_independent_tasks_share_one_layer():
    """同层可并发 —— 这是「所有任务并行跑」的落点。"""
    layers = topo_layers([_T("T1"), _T("T2"), _T("T3")])
    assert len(layers) == 1 and len(layers[0]) == 3


def test_dependency_forces_serial_layers():
    layers = topo_layers([_T("T1"), _T("T2", ["T1"]), _T("T3", ["T2"])])
    assert [[t.key for t in l] for l in layers] == [["T1"], ["T2"], ["T3"]]


def test_diamond_dependency():
    layers = topo_layers([_T("T1"), _T("T2", ["T1"]), _T("T3", ["T1"]),
                          _T("T4", ["T2", "T3"])])
    assert [sorted(t.key for t in l) for l in layers] == [["T1"], ["T2", "T3"], ["T4"]]


def test_cycle_degrades_to_serial_not_deadlock():
    """拆解 agent 偶尔会产出环 —— 不能因此卡死整条需求。"""
    layers = topo_layers([_T("T1", ["T2"]), _T("T2", ["T1"])])
    assert sum(len(l) for l in layers) == 2


# ── ticket 查找位置 ─────────────────────────────────────────────
class _WS:
    def __init__(self, root, repos):
        self.root = root
        self.repos = repos


def test_finds_tickets_written_inside_the_repo_dir(tmp_path):
    """**agent 通常把 .scratch 写进仓库目录，不是工位根。**

    opencode 的项目根检测会落到 git 仓那一层。只查工位根会把
    一份好拆解判成「未产出 ticket」—— 实测踩过。
    """
    from vplatform.orchestration.stages import find_tickets

    repo = tmp_path / "doBuyRight"
    issues = repo / ".scratch" / "r1" / "issues"
    issues.mkdir(parents=True)
    (issues / "01-x.md").write_text("# 01: 加胜率\n\n**Touches:**\n- `backend/m.py`\n",
                                    encoding="utf-8")

    ws = _WS(tmp_path, {"doBuyRight": str(repo)})
    tickets = find_tickets(ws, ".scratch/r1/issues")
    assert [t.title for t in tickets] == ["加胜率"]


def test_finds_tickets_at_workspace_root_too(tmp_path):
    from vplatform.orchestration.stages import find_tickets

    issues = tmp_path / ".scratch" / "r1" / "issues"
    issues.mkdir(parents=True)
    (issues / "01-y.md").write_text("# 01: 在工位根\n", encoding="utf-8")
    ws = _WS(tmp_path, {"repo": str(tmp_path / "repo")})
    assert [t.title for t in find_tickets(ws, ".scratch/r1/issues")] == ["在工位根"]


def test_same_ticket_in_both_places_is_deduped(tmp_path):
    from vplatform.orchestration.stages import find_tickets

    repo = tmp_path / "repo"
    for base in (tmp_path, repo):
        d = base / ".scratch" / "r1" / "issues"
        d.mkdir(parents=True)
        (d / "01-z.md").write_text("# 01: 同一份\n", encoding="utf-8")
    ws = _WS(tmp_path, {"repo": str(repo)})
    assert len(find_tickets(ws, ".scratch/r1/issues")) == 1


# ── Sequence 字段必须规范化 ──────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("expand", "expand"),
    ("migrate | 第 2 批", "migrate"),
    ("contract", "contract"),
    ("n/a（非 wide refactor，单 ticket 直落）", None),   # 实测 agent 真这么写
    ("无", None), ("none", None), ("", None), (None, None),
    ("EXPAND", "expand"),
])
def test_sequence_is_normalised(raw, want):
    """**不能把原文直接塞进去。**

    这一列是 `String(16)`，agent 会往里写整句话 —— 实测写过
    `n/a（非 wide refactor，单 ticket 直落）`，直接
    `Data too long for column 'sequence'`，**整个拆解环节炸掉**，
    而报错跟「拆解」毫无关系，排查的人只看到一条 SQL 异常。
    """
    from vplatform.orchestration.stages import _norm_sequence

    assert _norm_sequence(raw) == want


def test_a_long_sequence_value_never_reaches_the_column():
    """任何输入都不能超过列宽。"""
    from vplatform.orchestration.stages import _norm_sequence

    v = _norm_sequence("x" * 500)
    assert v is None or len(v) <= 16
