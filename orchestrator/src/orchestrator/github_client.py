"""github_client —— PAT 鉴权下的 git clone / push / fetch + GitHub REST API。

Plan 11 · M1.T3.

设计：
- 业务员的 PAT 只在请求时透传（不入 DB，扩展端存 chrome.storage.session）。
- 所有 git CLI 调用走 `git -c http.extraHeader="Authorization: bearer <PAT>" ...`
  形式，**不把 token 嵌入 remote URL** —— 避免 `git remote -v` / log 泄漏。
- 对 SSH 形式的 URL（git@...），http.extraHeader 是 no-op，假定系统 ssh-agent
  已配好 key（业务员的本地 SSH 私钥）。本模块不处理 SSH key。
- HTTP API 调用走 httpx.AsyncClient（与 _llm.py 风格一致）。
- 错误归一化成具体异常类型，调用方按需 catch。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

import httpx

# ── 异常 ─────────────────────────────────────────────────────────────


class GitHubError(Exception):
    """github_client 的基类。所有子异常都继承它，调用方可一把 catch。"""


class AuthError(GitHubError):
    """PAT 无效 / 被撤 / 缺权限（401 / 403 forbidden）。"""


class RateLimitError(GitHubError):
    """GitHub API rate limit 击中（403 with x-ratelimit-remaining: 0）。"""


class NotFoundError(GitHubError):
    """仓库不存在 / PAT 看不到这个 repo（404）。"""


class GitOperationError(GitHubError):
    """git CLI 子进程 rc != 0。message 含原始 stderr。"""

    def __init__(self, cmd: list[str], rc: int, stderr: str) -> None:
        super().__init__(
            f"git {' '.join(cmd[1:])} 失败 (rc={rc}): {stderr.strip() or '(无 stderr)'}"
        )
        self.cmd = cmd
        self.rc = rc
        self.stderr = stderr


# ── URL 工具 ─────────────────────────────────────────────────────────

_HTTPS_RE = re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$")
_SSH_RE = re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$")


def parse_github_url(url: str) -> tuple[str, str]:
    """从 git URL 拆出 (owner, repo)。同时支持 https + ssh 两种形式。

    >>> parse_github_url("https://github.com/weizhanhao/vibe-niuma.git")
    ('weizhanhao', 'vibe-niuma')
    >>> parse_github_url("git@github.com:weizhanhao/vibe-niuma.git")
    ('weizhanhao', 'vibe-niuma')
    """
    for pat in (_HTTPS_RE, _SSH_RE):
        m = pat.match(url.strip())
        if m:
            return m.group("owner"), m.group("repo")
    raise ValueError(f"不像 GitHub URL：{url!r}（支持 https://github.com/o/r 或 git@github.com:o/r）")


def _git_with_pat(pat: Optional[str]) -> list[str]:
    """给 git 命令拼前缀 `-c http.extraHeader="Authorization: bearer <PAT>"`。

    PAT 为 None 时不加 header（走系统 ssh / 公开仓 / 已 cache 的 credential）。
    对 SSH URL 是 no-op（git 走 ssh 协议不读 http config）。
    """
    if not pat:
        return ["git"]
    return ["git", "-c", f"http.extraHeader=Authorization: bearer {pat}"]


def _run_git(cmd: list[str], *, cwd: Optional[Path] = None, timeout: float = 600.0) -> str:
    """跑一条 git，rc != 0 抛 GitOperationError；正常返回 stdout 字符串。"""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise GitOperationError(cmd, proc.returncode, proc.stderr)
    return proc.stdout


# ── Git CLI 操作（同步，pipeline 用 asyncio.to_thread 包） ─────────


def clone(url: str, target_dir: Path, *, pat: Optional[str] = None, bare: bool = False) -> None:
    """clone 到 target_dir。bare=True 时是 --bare（用做 cache 后再 worktree add）。

    target_dir 必须不存在（git 会自己建）；存在时由调用方先 rm。
    """
    if target_dir.exists():
        raise ValueError(f"target_dir 已存在：{target_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = _git_with_pat(pat) + ["clone"]
    if bare:
        cmd.append("--bare")
    cmd += [url, str(target_dir)]
    _run_git(cmd)


def fetch(repo_dir: Path, *, pat: Optional[str] = None, remote: str = "origin") -> None:
    """git fetch <remote> --prune。"""
    cmd = _git_with_pat(pat) + ["fetch", remote, "--prune"]
    _run_git(cmd, cwd=repo_dir)


def push(
    repo_dir: Path,
    branch: str,
    *,
    pat: Optional[str] = None,
    set_upstream: bool = False,
    force: bool = False,
    remote: str = "origin",
) -> None:
    """git push [-u] [--force-with-lease] <remote> <branch>。"""
    cmd = _git_with_pat(pat) + ["push"]
    if set_upstream:
        cmd.append("-u")
    if force:
        cmd.append("--force-with-lease")
    cmd += [remote, branch]
    _run_git(cmd, cwd=repo_dir)


def remote_branch_exists(repo_dir: Path, branch: str, *, pat: Optional[str] = None, remote: str = "origin") -> bool:
    """探测远端是否已存在某分支。先 fetch 一下，再 ls-remote。"""
    fetch(repo_dir, pat=pat, remote=remote)
    cmd = _git_with_pat(pat) + ["ls-remote", "--heads", remote, branch]
    out = _run_git(cmd, cwd=repo_dir)
    return bool(out.strip())


# ── GitHub REST API（async，与 _llm.py 风格一致） ──────────────────


class GitHubAPI:
    """对 GitHub REST v3 的薄封装。所有方法 async。

    用法：
        api = GitHubAPI(pat=user_pat)
        user = await api.get_user()
        pr = await api.find_pr("weizhanhao", "vibe-niuma", head_branch="dev", base_branch="main")
    """

    def __init__(self, pat: str, *, base: str = "https://api.github.com", timeout: float = 30.0) -> None:
        if not pat:
            raise ValueError("PAT 不能为空")
        self._pat = pat
        self._base = base.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "vibe-niuma-orchestrator",
        }

    async def _request(self, method: str, path: str, *, json: Optional[dict] = None) -> dict | list:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as cli:
            resp = await cli.request(method, url, headers=self._headers(), json=json)
        self._raise_for_status(resp)
        if resp.status_code == 204:
            return {}
        return resp.json()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code == 401:
            raise AuthError("PAT 无效或已过期 (401)")
        if resp.status_code == 403:
            # 区分 rate limit vs forbidden
            if resp.headers.get("x-ratelimit-remaining") == "0":
                raise RateLimitError(f"GitHub API 限流，重置时间 {resp.headers.get('x-ratelimit-reset')}")
            raise AuthError(f"PAT 权限不足 (403): {resp.text[:200]}")
        if resp.status_code == 404:
            raise NotFoundError(f"资源不存在或 PAT 看不到 (404): {resp.url}")
        raise GitHubError(f"GitHub API 错误 {resp.status_code}: {resp.text[:200]}")

    async def get_user(self) -> dict:
        """GET /user —— 用来校验 PAT 是否有效（返 401 即无效）。

        返回含 `login` / `id` / `name` 等字段。
        """
        out = await self._request("GET", "/user")
        assert isinstance(out, dict)
        return out

    async def find_pr(
        self,
        owner: str,
        repo: str,
        *,
        head_branch: str,
        base_branch: str,
        state: str = "open",
    ) -> Optional[dict]:
        """找 head=<owner>:<head_branch> base=<base_branch> 的 PR。没有返 None。"""
        # GitHub 的 list PR 端点要 head=owner:branch
        path = (
            f"/repos/{owner}/{repo}/pulls"
            f"?state={state}&head={owner}:{head_branch}&base={base_branch}"
        )
        out = await self._request("GET", path)
        assert isinstance(out, list)
        return out[0] if out else None

    async def create_pr(
        self,
        owner: str,
        repo: str,
        *,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> dict:
        """POST /repos/{owner}/{repo}/pulls 创建一个 PR。返回完整 PR 对象。"""
        payload = {
            "title": title,
            "head": head_branch,
            "base": base_branch,
            "body": body,
            "draft": draft,
        }
        out = await self._request("POST", f"/repos/{owner}/{repo}/pulls", json=payload)
        assert isinstance(out, dict)
        return out

    async def update_pr(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        body: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict:
        """PATCH /repos/{owner}/{repo}/pulls/{number}。只更新提供的字段。"""
        payload: dict = {}
        if body is not None:
            payload["body"] = body
        if title is not None:
            payload["title"] = title
        if not payload:
            raise ValueError("update_pr 至少要传一个字段")
        out = await self._request("PATCH", f"/repos/{owner}/{repo}/pulls/{pr_number}", json=payload)
        assert isinstance(out, dict)
        return out

    async def upsert_pr(
        self,
        owner: str,
        repo: str,
        *,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> tuple[dict, bool]:
        """找 head→base 的开放 PR：有就 update body / title 返 (pr, False)；
        没有就 create 返 (pr, True)。

        给 auto_pr.py 维护 long-running PR 用：业务员每合一条 CR 就 upsert 一次，
        body 累加这条 CR 的描述。
        """
        existing = await self.find_pr(owner, repo, head_branch=head_branch, base_branch=base_branch)
        if existing is not None:
            updated = await self.update_pr(owner, repo, existing["number"], body=body, title=title)
            return updated, False
        created = await self.create_pr(
            owner, repo, head_branch=head_branch, base_branch=base_branch, title=title, body=body
        )
        return created, True
