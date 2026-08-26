#!/usr/bin/env python3
"""接缝守卫 —— 核心层不得 import 任何具体实现。

**为什么这条要靠 CI 守，不能靠自觉**：v1 的 UI 文案承诺
「支持 GitHub / Gitee / 云效」，代码却在 `github_client.py:72` 对非 GitHub URL
直接 raise ValueError。承诺无处落地，因为根本没有接缝。

规则：受管层只能依赖 Protocol（*/adapter.py、*/provider.py、*/session.py），
不能依赖具体实现（hosts/github.py、review/ocr.py、deploy/selfhosted.py …）。

用法：python scripts/check_seams.py   —— 违规时非 0 退出并逐条列出
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "vplatform"

# 受管层 → 不许 import 的模块前缀
RULES: dict[str, tuple[str, ...]] = {
    "core": ("vplatform.hosts.", "vplatform.review.", "vplatform.deploy.",
             "vplatform.workspace.", "vplatform.agents.", "vplatform.merge.",
             "vplatform.api."),
    "orchestration": ("vplatform.hosts.github", "vplatform.review.ocr",
                      "vplatform.deploy.selfhosted",
                      "vplatform.workspace.worktree_docker",
                      "vplatform.agents.opencode"),
    "workspace": ("vplatform.hosts.github", "vplatform.review.", "vplatform.deploy.",
                  "vplatform.api."),
    "merge": ("vplatform.hosts.github", "vplatform.review.ocr", "vplatform.api."),
    "review": ("vplatform.hosts.github", "vplatform.deploy.", "vplatform.api."),
}

# 具体实现自己可以 import 自己那层的东西
EXEMPT_FILES = {"hosts/github.py", "review/ocr.py", "deploy/selfhosted.py",
                "workspace/worktree_docker.py", "agents/opencode.py"}


def imported_modules(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.module, node.lineno))
            # `from vplatform.workspace import worktree_docker` 的 module 是
            # `vplatform.workspace`，会绕过针对 `...worktree_docker` 的禁令。
            # 把 module.name 也算进来堵这个洞。
            for a in node.names:
                out.append((f"{node.module}.{a.name}", node.lineno))
    return out


def main() -> int:
    violations: list[str] = []
    for layer, banned in RULES.items():
        for py in sorted((SRC / layer).rglob("*.py")):
            rel = py.relative_to(SRC).as_posix()
            if rel in EXEMPT_FILES:
                continue
            for mod, lineno in imported_modules(py):
                for b in banned:
                    if mod == b.rstrip(".") or mod.startswith(b):
                        violations.append(
                            f"{rel}:{lineno} 层 `{layer}` 不得 import `{mod}` —— "
                            f"只能依赖 Protocol")
    if violations:
        print("接缝被打破：\n")
        for v in violations:
            print("  ✗", v)
        print("\n修法：把具体实现通过构造参数注入，不要在核心层直接 import。")
        return 1
    print(f"✓ 接缝完好（检查了 {len(RULES)} 层）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
