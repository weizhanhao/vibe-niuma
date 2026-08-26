"""GitHubHost —— GitHostAdapter 的第一个实现。

之前 `hosts/` 只有 Protocol，一个实现都没有。后果比"缺一个适配器"严重得多：
**没有 push 就没有交付** —— agent 的 commit 只存在于工位里，
release 时随 `shutil.rmtree` 一起删掉。改动的生命周期是
「产生 → 无人看见 → 被删除」。

命名保持中性（ChangeRef 而不是 PullRequest），云效/Gitee 将来只加实现文件。
"""
from __future__ import annotations

import asyncio
import json
import hmac
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from vplatform.hosts.adapter import ChangeRef, HostError

logger = logging.getLogger(__name__)

_SSH = re.compile(r"^git@([^:]+):(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")


def parse_repo(url: str) -> tuple[str, str]:
    """从 URL 取 owner/repo。**不做 host 校验** —— 那是 v1 的错误
    （`parse_github_url` 对非 GitHub URL 直接 raise，让"支持 Gitee/云效"
    的承诺无处落地）。这里只负责解析形状，host 由 adapter 选择决定。"""
    m = _SSH.match(url.strip())
    if m:
        return m.group("owner"), m.group("repo")
    path = urlparse(url).path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise HostError(f"无法从 {url!r} 解析出 owner/repo")
    return parts[-2], parts[-1]


def _auth_url(url: str, pat: str | None) -> str:
    if not pat or not url.startswith("https://"):
        return url
    return url.replace("https://", f"https://x-access-token:{pat}@", 1)


async def _git(cwd: Path | str | None, *args: str, timeout: float = 600):
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    rc = proc.returncode or 0
    if rc != 0:
        raise HostError(f"git {' '.join(args[:2])} 失败 (rc={rc}): "
                        f"{err.decode('utf-8', 'replace').strip()[:400]}")
    return out.decode("utf-8", "replace")


class GitHubHost:
    """实现 GitHostAdapter Protocol。"""

    def __init__(self, *, api_base: str = "https://api.github.com",
                 client: httpx.AsyncClient | None = None, timeout: float = 60):
        self.api = api_base.rstrip("/")
        self._client = client
        self.timeout = timeout

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def clone(self, url: str, dest: Path, *, bare: bool = False,
                    pat: str | None = None) -> None:
        argv = ["clone"]
        if bare:
            argv.append("--mirror")
        argv += [_auth_url(url, pat), str(dest)]
        await _git(None, *argv, timeout=1800)

    async def fetch(self, work_dir: Path) -> None:
        # 与 workspace 层一致：只取到 remotes 命名空间，不碰本地 refs/heads
        await _git(work_dir, "fetch", "--prune", "origin",
                   "+refs/heads/*:refs/remotes/origin/*", timeout=900)

    async def push(self, work_dir: Path, branch: str, *,
                   pat: str | None = None) -> None:
        """推分支。**这是「改动离开工位」的唯一出口。**"""
        if pat:
            url = (await _git(work_dir, "remote", "get-url", "origin")).strip()
            await _git(work_dir, "push", _auth_url(url, pat),
                       f"HEAD:refs/heads/{branch}", timeout=900)
        else:
            await _git(work_dir, "push", "origin", f"HEAD:refs/heads/{branch}",
                       timeout=900)

    async def open_change(self, *, repo_url: str, head: str, base: str, title: str,
                          body: str, pat: str | None = None) -> ChangeRef:
        owner, repo = parse_repo(repo_url)
        try:
            r = await self._c().post(
                f"{self.api}/repos/{owner}/{repo}/pulls",
                headers=self._headers(pat),
                json={"title": title, "head": head, "base": base, "body": body})
            if r.status_code == 422 and "already exists" in r.text:
                # 已存在就复用，不当失败 —— 重试路径会重复调这里
                lst = await self._c().get(
                    f"{self.api}/repos/{owner}/{repo}/pulls",
                    headers=self._headers(pat),
                    params={"head": f"{owner}:{head}", "base": base, "state": "open"})
                lst.raise_for_status()
                items = lst.json()
                if items:
                    d = items[0]
                    return ChangeRef(id=str(d["id"]), number=d["number"],
                                     url=d["html_url"])
            r.raise_for_status()
            d = r.json()
            return ChangeRef(id=str(d["id"]), number=d["number"], url=d["html_url"])
        except httpx.HTTPError as exc:
            raise HostError(f"创建变更请求失败: {exc}") from exc

    async def comment(self, change: ChangeRef, body: str, *,
                      pat: str | None = None) -> None:
        if not change.url:
            raise HostError("ChangeRef 没有 url，无法评论")
        owner, repo = parse_repo(change.url)
        try:
            r = await self._c().post(
                f"{self.api}/repos/{owner}/{repo}/issues/{change.number}/comments",
                headers=self._headers(pat), json={"body": body})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HostError(f"评论失败: {exc}") from exc

    def verify_webhook(self, headers: dict, raw: bytes, *, secret: str) -> bool:
        """校验 webhook 签名。**用 compare_digest 防时序攻击。**"""
        sig = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256")
        if not sig or not secret:
            return False
        expect = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expect)

    def _headers(self, pat: str | None) -> dict:
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"}
        if pat:
            h["Authorization"] = f"Bearer {pat}"
        return h

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
