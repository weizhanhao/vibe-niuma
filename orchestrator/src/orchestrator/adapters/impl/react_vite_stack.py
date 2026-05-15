"""ReactViteStackAdapter —— Plan 1 demo 应用的 StackAdapter 实现。

URL → 源文件定位走「集中式路由表」约定：扫描 demo `frontend/src` 下的路由文件
（router.tsx / App.tsx / routes.tsx），用正则把 `path → 组件` 与 `import` 表
拼出 `path → 源文件`。MVP 用正则即可，demo 路由表是已知契约基准。

如果换个前端框架（Vue / Next 等），新写一个 StackAdapter 实现即可，主流水线不变。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from orchestrator.adapters.types import (
    BuildResult,
    DevContext,
    LocateResult,
    RawRequest,
    RequestBrief,
)


# 路由文件的候选位置（按优先级）
_ROUTER_CANDIDATES = (
    "frontend/src/router.tsx",
    "frontend/src/router.ts",
    "frontend/src/routes.tsx",
    "frontend/src/routes.ts",
    "frontend/src/App.tsx",
)

# 匹配两种常见路由表写法：
#   { path: '/x', element: <Foo /> }
#   <Route path="/x" element={<Foo/>} />
_ROUTE_PATTERNS = (
    re.compile(
        r"""path\s*:\s*['"]([^'"]+)['"][\s\S]{0,160}?element\s*:\s*<\s*(\w+)\s*/?\s*>""",
        re.M,
    ),
    re.compile(
        r"""<Route\s+[^>]*?path\s*=\s*['"]([^'"]+)['"][\s\S]{0,160}?element\s*=\s*\{?\s*<\s*(\w+)\s*/?\s*>""",
        re.M,
    ),
)

# 匹配 named import:  import { Foo } from './bar';
_IMPORT_NAMED = re.compile(
    r"""import\s*\{\s*([^}]+?)\s*\}\s*from\s*['"]([^'"]+)['"]""",
    re.M,
)
# 匹配 default import: import Foo from './bar';
_IMPORT_DEFAULT = re.compile(
    r"""import\s+(\w+)\s+from\s*['"]([^'"]+)['"]""",
    re.M,
)


@dataclass
class _Route:
    path: str
    component: str


class ReactViteStackAdapter:
    """实现 StackAdapter Protocol：locate / context_pack / build。"""

    def __init__(self, repo_path: str):
        self._repo = Path(repo_path)

    # ── locate ──────────────────────────────────────────────────────
    async def locate(self, url: str) -> LocateResult:
        path = urlparse(url).path or "/"
        router = self._find_router_file()
        if router is None:
            return LocateResult(entry_files=[], route_path="")

        text = router.read_text(encoding="utf-8")
        routes = self._parse_routes(text)
        imports = self._parse_imports(text)

        match = _match_route(path, routes)
        if match is None:
            return LocateResult(entry_files=[], route_path="")

        component_file = self._resolve_component_file(router, imports, match.component)
        if component_file is None:
            # 路由匹配上但找不到对应文件：仍把 router 作为入口返回（弱信号）
            return LocateResult(
                entry_files=[self._rel(router)],
                route_path=match.path,
            )
        return LocateResult(
            entry_files=[self._rel(component_file)],
            route_path=match.path,
        )

    # ── context_pack ────────────────────────────────────────────────
    async def context_pack(
        self, locate_result: LocateResult, raw: RawRequest, brief: RequestBrief
    ) -> DevContext:
        """读 entry_files 内容 + 一层本地 import 展开（只跟相对路径）。"""
        contents: dict[str, str] = {}
        for rel in locate_result.entry_files:
            p = self._repo / rel
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            contents[rel] = text
            for nested in self._expand_local_imports(p, text):
                if nested is None:
                    continue
                rel_nested = self._rel(nested)
                if rel_nested in contents:
                    continue
                try:
                    contents[rel_nested] = nested.read_text(encoding="utf-8")
                except OSError:
                    continue
        return DevContext(
            brief=brief,
            locate_result=locate_result,
            screenshot_b64=raw.screenshot_b64,
            box_coords=raw.box_coords,
            entry_file_contents=contents,
        )

    def _expand_local_imports(self, src: Path, text: str):
        """提取 src 文件里的相对 import，返回解析后的真实文件路径。"""
        seen_specs = set()
        for m in _IMPORT_NAMED.finditer(text):
            seen_specs.add(m.group(2))
        for m in _IMPORT_DEFAULT.finditer(text):
            seen_specs.add(m.group(2))
        for spec in seen_specs:
            if not spec.startswith((".", "/")):
                continue
            base = (src.parent / spec).resolve()
            yield self._resolve_module_file(base)

    @staticmethod
    def _resolve_module_file(base: Path) -> Path | None:
        if base.is_file():
            return base
        for ext in (".tsx", ".ts", ".jsx", ".js"):
            p = base.parent / (base.name + ext)
            if p.is_file():
                return p
        for ext in (".tsx", ".ts", ".jsx", ".js"):
            p = base / f"index{ext}"
            if p.is_file():
                return p
        return None

    # ── build ───────────────────────────────────────────────────────
    async def build(self, repo_path: str, branch: str) -> BuildResult:
        """前端 npm ci + npm run build；后端 python -m compileall。"""
        import asyncio
        import os

        repo = Path(repo_path)
        log_parts: list[str] = []
        ok = True

        async def _run(cmd: list[str], cwd: Path, label: str) -> tuple[int, str]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(cwd),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "CI": "1"},
                )
                stdout, _ = await proc.communicate()
                return proc.returncode, f"--- {label} ---\n{stdout.decode(errors='replace')}\n"
            except FileNotFoundError as exc:
                return 127, f"--- {label} ---\n{exc}\n"

        frontend = repo / "frontend"
        if frontend.is_dir():
            if not (frontend / "node_modules").is_dir():
                rc, log = await _run(["npm", "ci"], frontend, "npm ci")
                log_parts.append(log)
                if rc != 0:
                    ok = False
            if ok:
                rc, log = await _run(["npm", "run", "build"], frontend, "npm run build")
                log_parts.append(log)
                if rc != 0:
                    ok = False

        backend = repo / "backend"
        if ok and backend.is_dir():
            rc, log = await _run(
                ["python3", "-m", "compileall", "-q", "."], backend, "compileall backend"
            )
            log_parts.append(log)
            if rc != 0:
                ok = False

        return BuildResult(ok=ok, log="".join(log_parts) or "no build steps ran")

    # ── helpers ─────────────────────────────────────────────────────
    def _find_router_file(self) -> Path | None:
        for c in _ROUTER_CANDIDATES:
            p = self._repo / c
            if p.exists():
                return p
        return None

    @staticmethod
    def _parse_routes(text: str) -> list[_Route]:
        out: list[_Route] = []
        seen: set[tuple[str, str]] = set()
        for pat in _ROUTE_PATTERNS:
            for m in pat.finditer(text):
                key = (m.group(1), m.group(2))
                if key in seen:
                    continue
                seen.add(key)
                out.append(_Route(path=m.group(1), component=m.group(2)))
        return out

    @staticmethod
    def _parse_imports(text: str) -> dict[str, str]:
        """component name → module specifier (relative path string)。"""
        imports: dict[str, str] = {}
        for m in _IMPORT_NAMED.finditer(text):
            for name in (n.strip() for n in m.group(1).split(",") if n.strip()):
                # `Foo as Bar` —— 取 Bar
                key = name.split(" as ")[-1].strip()
                imports[key] = m.group(2)
        for m in _IMPORT_DEFAULT.finditer(text):
            imports[m.group(1)] = m.group(2)
        return imports

    def _resolve_component_file(
        self, router: Path, imports: dict[str, str], component: str
    ) -> Path | None:
        spec = imports.get(component)
        if not spec:
            return None
        # 仅处理相对路径；@alias / 第三方略过
        if not spec.startswith((".", "/")):
            return None
        base = (router.parent / spec).resolve()
        # 直接命中（spec 已含扩展名）
        if base.is_file():
            return base
        # 加扩展名
        for ext in (".tsx", ".ts", ".jsx", ".js"):
            p = base.parent / (base.name + ext)
            if p.is_file():
                return p
        # 目录 + index
        for ext in (".tsx", ".ts", ".jsx", ".js"):
            p = base / f"index{ext}"
            if p.is_file():
                return p
        return None

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self._repo))


def _match_route(url_path: str, routes: list[_Route]) -> _Route | None:
    """按 React Router v6 规则匹配：静态段精确，`:param` 通配；最长匹配优先。"""
    url_segs = [s for s in url_path.split("/") if s] or [""]
    candidates: list[tuple[int, _Route]] = []
    for r in routes:
        route_segs = [s for s in r.path.split("/") if s] or [""]
        if len(route_segs) != len(url_segs):
            continue
        ok = True
        score = 0
        for rs, us in zip(route_segs, url_segs):
            if rs.startswith(":"):
                continue  # 动态段：通配
            if rs != us:
                ok = False
                break
            score += 1  # 静态段命中加分（更具体）
        if ok:
            candidates.append((score, r))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
