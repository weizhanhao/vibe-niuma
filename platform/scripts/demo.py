#!/usr/bin/env python3
"""端到端 demo —— 用真 git 仓、真隔离工位跑一条需求。

    python scripts/demo.py isolation   只跑工位隔离（无需 LLM，秒级）
    python scripts/demo.py conflict    冲突三档处理（无需 LLM）
    python scripts/demo.py review      真实 ocr 复核（需要 DASHSCOPE_API_KEY）
    python scripts/demo.py full        全部

环境变量：
    VP_DEMO_REPOS        demo 目标仓所在目录（默认 <repo>/demo-target）
    DASHSCOPE_API_KEY    review / agent 环节需要
    VP_OCR_BIN           ocr 可执行文件路径
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "src"))

from vplatform.core import db as dbmod                                   # noqa: E402
from vplatform.core.models import (                                       # noqa: E402
    Member, Org, Project, ProjectRepo, Requirement, Task, TaskTouch,
    next_requirement_seq,
)
from vplatform.merge.conflict import ConflictLadder                       # noqa: E402
from vplatform.workspace.provider import RepoSpec                         # noqa: E402
from vplatform.workspace.worktree_docker import WorktreeDockerProvider    # noqa: E402

DEMO_REPOS = Path(os.environ.get("VP_DEMO_REPOS", ROOT / "demo-target"))


def hr(title: str) -> None:
    print(f"\n{'═' * 68}\n  {title}\n{'═' * 68}")


def git(repo, *args, check: bool = True):
    """**默认 check=True。**

    第一版这个 helper 吞掉了错误 —— `checkout vibe/dev` 在 clone 后失败
    （它只是远程分支），后面所有步骤都在 main 上跑，demo 还显示「通过」。
    静默失败比崩溃难查得多。
    """
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} 失败 (rc={r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[:300]}")
    return r


def clone_demo(work: Path, name: str) -> Path:
    """clone 一个 demo 仓并建好本地 vibe/dev。

    clone 之后 `vibe/dev` 只存在于 refs/remotes/origin/ —— 且名字带斜杠，
    git 的 DWIM 不会自动建本地分支。必须显式从 origin/ 建。
    """
    repo = work / name
    if repo.exists():
        subprocess.run(["rm", "-rf", str(repo)], check=True)
    work.mkdir(parents=True, exist_ok=True)
    git(work, "clone", "-q", str(DEMO_REPOS / name), name)
    git(repo, "config", "user.email", "demo@vp")
    git(repo, "config", "user.name", "demo")
    git(repo, "checkout", "-q", "-B", "vibe/dev", "origin/vibe/dev")
    return repo


# ── 1. 工位隔离 ─────────────────────────────────────────────────
async def demo_isolation(root: Path) -> None:
    hr("Demo 1 · 并行工位隔离（M2 分水岭）")
    print("v1 只有一个工作树，create_branch 会 stash + reset --hard + clean ——")
    print("第二个 Run 直接抹掉第一个 agent 正在写的文件。这里每条轨道各有各的工位。\n")

    # 容器在本机 demo 里关掉：worktree 隔离是重点，容器是生产时的第二层
    prov = WorktreeDockerProvider(root=root, use_container=False)
    specs = [RepoSpec(name="orders-api", url=str(DEMO_REPOS / "orders-api")),
             RepoSpec(name="orders-web", url=str(DEMO_REPOS / "orders-web"))]

    handles = []
    for i in range(1, 6):
        h = await prov.acquire(project_id="mc", run_id=f"run{i}",
                               branch=f"cr/1-t{i}", base_branch="vibe/dev",
                               repos=specs)
        handles.append(h)
        # 每个工位改同一个文件的同一行 —— 最容易互相踩的场景
        p = Path(h.repos["orders-api"], "app", "store.py")
        p.write_text(p.read_text(encoding="utf-8").replace(
            "def all_orders()", f"def all_orders()  # 工位 {i} 到此一游"),
            encoding="utf-8")
        print(f"  工位 {i}  {h.repos['orders-api'].replace(str(root), '<root>')}")

    print("\n  各工位内容互不可见：")
    ok = True
    for i, h in enumerate(handles, 1):
        text = Path(h.repos["orders-api"], "app", "store.py").read_text(encoding="utf-8")
        mine = f"工位 {i} 到此一游" in text
        others = [j for j in range(1, 6) if j != i and f"工位 {j} 到此一游" in text]
        print(f"    工位 {i}：自己的改动 {'在' if mine else '丢了'}"
              f" · 别人的改动 {'串进来了 ' + str(others) if others else '没串进来'}")
        ok = ok and mine and not others

    mirrors = list((root / "mc" / "mirrors").glob("*.git"))
    print(f"\n  共享 object store：{len(mirrors)} 个 bare mirror 服务 5 个工位")
    print(f"  结论：{'✓ 5 条轨道并行互不污染' if ok else '✗ 隔离失效'}")

    for h in handles:
        await prov.release(h)
    print("  工位已全部回收")
    return ok


# ── 2. 冲突三档 ─────────────────────────────────────────────────
async def demo_conflict(root: Path) -> None:
    hr("Demo 2 · 三档递进的冲突处理（§12）")
    repo = clone_demo(root / "conflict-demo", "orders-api")

    # 两条需求改同一个函数 —— 真冲突
    git(repo, "checkout", "-qb", "cr/2-t1", "vibe/dev")
    p = repo / "app" / "store.py"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "def query(status: str | None = None) -> list[Order]:",
        "def query(status: str | None = None, store_id: str | None = None) -> list[Order]:"),
        encoding="utf-8")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "feat: 门店筛选")

    git(repo, "checkout", "-q", "vibe/dev")
    p.write_text(p.read_text(encoding="utf-8").replace(
        "def query(status: str | None = None) -> list[Order]:",
        "def query(status: str | None = None, limit: int = 50) -> list[Order]:"),
        encoding="utf-8")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "feat: 分页")

    # 自检：两边的改动必须真的在各自分支上，否则后面测的是空气
    a = git(repo, "show", "cr/2-t1:app/store.py").stdout
    b = git(repo, "show", "vibe/dev:app/store.py").stdout
    assert "store_id" in a, "cr/2-t1 的改动没落进 commit"
    assert "limit: int" in b, "vibe/dev 的改动没落进 commit"
    print("  cr/2-t1 加了 store_id 参数，vibe/dev 同一行加了 limit —— 真语义冲突\n")

    async def fake_ai(repo_path, files, session_id):
        """demo 用确定性替身。真实实现是带原会话的 agent —— 它知道自己当初为什么这么改。"""
        f = Path(repo_path) / files[0]
        f.write_text(f.read_text(encoding="utf-8").replace(
            "<<<<<<< HEAD", "").replace("=======", "").replace(">>>>>>>", "")
            .replace("def query(status: str | None = None, limit: int = 50) -> list[Order]:", "")
            .replace("def query(status: str | None = None, store_id: str | None = None) -> list[Order]:",
                     "def query(status: str | None = None, store_id: str | None = None,\n"
                     "          limit: int = 50) -> list[Order]:"),
            encoding="utf-8")
        # 清掉残留冲突标记行
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines()
                 if not ln.startswith(("<<<", "===", ">>>"))]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        git(repo_path, "add", "-A", check=False)
        return []

    ladder = ConflictLadder(ai_resolver=fake_ai)
    res = await ladder.resolve(repo, onto="vibe/dev", branch="cr/2-t1",
                               session_id="ses_orig_a91c2f")
    for r in res.rungs:
        print(f"  {'✓' if r.ok else '◐'} {r.stage:18} {r.detail}")
    print(f"\n  结论：{'✓ 冲突已解决' if res.resolved else '✗ 仍有冲突，已 abort'}")
    if res.resolved:
        print("  合并后的签名：")
        for ln in (repo / "app" / "store.py").read_text(encoding="utf-8").splitlines():
            if "def query(" in ln or "limit: int" in ln:
                print(f"    {ln}")
    return res.resolved


# ── 3. 真实 ocr 复核 ────────────────────────────────────────────
async def demo_review(root: Path) -> None:
    hr("Demo 3 · 真实 AI 复核（ocr + 自建过滤层）")
    key = os.environ.get("DASHSCOPE_API_KEY")
    ocr_bin = os.environ.get("VP_OCR_BIN", "ocr")
    if not key:
        print("  跳过：需要 DASHSCOPE_API_KEY")
        return None

    from vplatform.review.adapter import build_background
    from vplatform.review.filter import FindingFilter
    from vplatform.review.ocr import OcrReviewAdapter

    repo = clone_demo(root / "review-demo", "orders-api")

    # 埋一个真 bug：契约说金额是元，这里直接返回分
    git(repo, "checkout", "-qb", "cr/3-t1", "vibe/dev")
    p = repo / "app" / "routers" / "orders.py"
    p.write_text(p.read_text(encoding="utf-8")
                 .replace('"amount": o.amount_yuan,', '"amount": o.amount_cents,')
                 .replace('def list_orders(status: str | None = None) -> list[dict]:',
                          'def list_orders(status: str | None = None,\n'
                          '                store_id: str | None = None) -> list[dict]:'),
                 encoding="utf-8")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "feat: 门店筛选 + 金额字段")
    print("  改动：加了 store_id 参数，同时把 amount 从元改成了分（违反 CONTEXT.md 的约定）\n")

    bg = build_background(
        title="订单列表支持按门店筛选",
        body="区域经理要按自己片区看数。金额展示口径不变。",
        contracts=["GET /orders?status=&storeId= → Order[]，amount 单位为元"])

    adapter = OcrReviewAdapter(binary=ocr_bin, use_builtin_filter=False,
                               env={"HOME": os.environ.get("HOME", "")})
    print("  跑 ocr（--no-filter，自建过滤层接管）…")
    res = await adapter.review(repo_path=str(repo), base="vibe/dev", head="cr/3-t1",
                               background=bg, token_budget=120_000)
    print(f"  ocr：{len(res.findings)} 条原始发现 · {res.tokens:,} token · {res.elapsed}")
    if res.degraded:
        print(f"  ⚠ 降级运行：上游 {res.failed_requests} 个请求失败")

    if res.findings:
        flt = FindingFilter(
            endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            api_key=key, model="deepseek-v4-pro")
        findings = await flt.apply(res.findings)
        await flt.aclose()
        print("\n  自建过滤裁决：")
        for f in findings:
            mark = "保留" if f.kept else "丢弃"
            print(f"    [{mark}] {f.severity:8} {f.path}:{f.start_line}")
            print(f"           {f.claim[:110]}")
            print(f"           ← {f.verdict_reason}")
        kept = [f for f in findings if f.kept]
        print(f"\n  结论：留 {len(kept)} · 丢 {len(findings) - len(kept)}")
        return len(kept) > 0
    print("\n  ocr 未发现问题。注意：**这不等于代码没问题** ——")
    print("  实测同一份 diff 三次跑出 2/0/0，召回不稳定（用召回换精确的取舍）。")
    return None


async def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "full"
    if not DEMO_REPOS.is_dir():
        print(f"找不到 demo 目标仓：{DEMO_REPOS}")
        return 2

    with tempfile.TemporaryDirectory(prefix="vp-demo-") as tmp:
        root = Path(tmp)
        results = {}
        if which in ("isolation", "full"):
            results["工位隔离"] = await demo_isolation(root)
        if which in ("conflict", "full"):
            results["冲突三档"] = await demo_conflict(root)
        if which in ("review", "full"):
            results["AI 复核"] = await demo_review(root)

        hr("小结")
        for k, v in results.items():
            print(f"  {k:12} {'✓ 通过' if v else '— 跳过' if v is None else '✗ 失败'}")
        return 0 if all(v is not False for v in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
