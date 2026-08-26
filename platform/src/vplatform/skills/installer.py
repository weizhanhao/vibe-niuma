"""Skill 层三层安装（§14.3）。

opencode 的发现顺序（项目级从 cwd 往上走到 git worktree 根）：

    项目级  .opencode/skills/ > .claude/skills/ > .agents/skills/
    全局    ~/.config/opencode/skills/ > ~/.claude/skills/ > ~/.agents/skills/

正好切成三层，**默认优先级顺序就是我们要的，不用调**：

    L1 平台级   容器镜像 ~/.config/opencode/skills/   流程环节用的 skill，不污染客户仓库
    L2 空间级   worktree .opencode/skills/            每个空间自己的规范
    L3 仓库自带 客户仓库 .claude/skills/               天然被发现，优先级最高

客户仓库自己的规范覆盖平台默认 —— 这正是想要的行为。
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

L1_GLOBAL = Path(".config/opencode/skills")     # 相对 HOME
L2_PROJECT = Path(".opencode/skills")           # 相对 worktree 根
L3_REPO = Path(".claude/skills")                # 客户仓库自带


@dataclass(frozen=True)
class InstalledSkill:
    name: str
    layer: str          # L1 | L2 | L3
    path: Path


def _read_name(skill_md: Path) -> str | None:
    """从 frontmatter 读 name。缺 name 的 skill 不装 —— opencode 也认不出来。"""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    for line in text[3:end].splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def discover(root: Path) -> dict[str, Path]:
    """扫一个 skills 目录，返回 {name: 目录}。"""
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        md = d / "SKILL.md"
        if not d.is_dir() or not md.is_file():
            continue
        name = _read_name(md) or d.name
        out[name] = d
    return out


def install_platform_skills(dist: Path, home: Path) -> list[InstalledSkill]:
    """L1：把 platform-skills/dist 装进容器的全局目录。

    这一层不进客户仓库 —— 客户 clone 下来不会看到我们塞的东西。
    """
    target = home / L1_GLOBAL
    target.mkdir(parents=True, exist_ok=True)
    out = []
    for name, src in discover(dist).items():
        dest = target / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        out.append(InstalledSkill(name, "L1", dest))
    return out


def install_project_skills(specs: dict[str, str], worktree: Path) -> list[InstalledSkill]:
    """L2：把空间级 skill 写进 worktree 的 .opencode/skills/。

    specs: {skill 名: SKILL.md 全文}。内容来自 Project 配置，不是文件系统 ——
    这样一个空间的规范可以在 Web 里改，不用重建镜像。
    """
    target = worktree / L2_PROJECT
    target.mkdir(parents=True, exist_ok=True)
    out = []
    for name, body in specs.items():
        d = target / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        out.append(InstalledSkill(name, "L2", d))
    return out


def effective_skills(*, home: Path, worktree: Path) -> dict[str, InstalledSkill]:
    """算出 agent 实际会看到的 skill 集合与来源层。

    优先级 L3 > L2 > L1 —— 客户仓库自己的规范覆盖平台默认。
    部署前用它自检：流水线要的 skill 是不是都在，有没有被客户仓库意外覆盖。
    """
    merged: dict[str, InstalledSkill] = {}
    for layer, root in (("L1", home / L1_GLOBAL),
                        ("L2", worktree / L2_PROJECT),
                        ("L3", worktree / L3_REPO)):
        for name, path in discover(root).items():
            merged[name] = InstalledSkill(name, layer, path)   # 后面的覆盖前面的
    return merged


def verify_pipeline_skills(pipeline, *, home: Path, worktree: Path) -> list[str]:
    """返回流水线要但装不到的 skill 名。空列表 = 齐了。

    **在跑之前查**，别等 agent 调用时才发现 skill 不存在 —— 那时它会自己瞎编一套做法。
    """
    have = effective_skills(home=home, worktree=worktree)
    return sorted(pipeline.required_skills - set(have))
