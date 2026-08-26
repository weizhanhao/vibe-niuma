"""接缝守卫本身也要测 —— 一个永远返回 0 的守卫等于没有守卫。"""
import subprocess
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
CHECKER = PLATFORM / "scripts" / "check_seams.py"


def test_current_codebase_has_intact_seams():
    r = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_checker_actually_catches_a_violation(tmp_path, monkeypatch):
    """种一个违规进去，守卫必须报出来。"""
    src = tmp_path / "src" / "vplatform"
    (src / "core").mkdir(parents=True)
    (src / "hosts").mkdir(parents=True)
    (src / "core" / "bad.py").write_text(
        "from vplatform.hosts.github import GitHubHost\n", encoding="utf-8")

    script = CHECKER.read_text(encoding="utf-8").replace(
        'SRC = Path(__file__).resolve().parents[1] / "src" / "vplatform"',
        f'SRC = Path({str(src)!r})')
    probe = tmp_path / "probe.py"
    probe.write_text(script, encoding="utf-8")

    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True)
    assert r.returncode == 1
    assert "core/bad.py" in r.stdout
    assert "只能依赖 Protocol" in r.stdout
