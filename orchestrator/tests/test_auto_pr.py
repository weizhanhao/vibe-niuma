"""auto_pr 测试 —— 验证 PR body 渲染 + maintain_pr 委托。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from orchestrator.auto_pr import (
    CRRecord,
    MAX_PR_BODY_CHARS,
    build_pr_body,
    build_pr_title,
    maintain_pr,
)


def _make_record(i: int, *, text: str = "改红色") -> CRRecord:
    return CRRecord(
        cr_id=f"cr-{i:03d}",
        business_text=text,
        commit_sha=f"deadbeef{i:04x}cafe",
        timestamp_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00",
        screenshot_url=f"https://example.com/s/{i}.png" if i % 2 == 0 else None,
    )


# ── build_pr_title ──────────────────────────────────────────────────


def test_pr_title_zero_records():
    assert build_pr_title([]) == "[vibe-niuma] 业务员合并请求"


def test_pr_title_with_count():
    assert build_pr_title([_make_record(1), _make_record(2)]) == "[vibe-niuma] 业务员合并请求 (2 条改动)"


# ── build_pr_body ──────────────────────────────────────────────────


def test_pr_body_empty_records_shows_placeholder():
    body = build_pr_body([], target_branch="vibe-niuma/dev", base_branch="main")
    assert "业务员还没合并过 CR" in body
    assert "vibe-niuma/dev" in body
    assert "main" in body


def test_pr_body_renders_single_record():
    rec = _make_record(1, text="订单徽章改红色")
    body = build_pr_body([rec], target_branch="vibe-niuma/dev", base_branch="main")
    assert "#cr-001" in body
    assert "订单徽章改红色" in body
    assert "deadbeef00" in body  # commit prefix
    assert "📷 截图" not in body  # i=1 是奇数，没截图


def test_pr_body_includes_screenshot_link():
    rec = _make_record(2)  # i=2 → 有 screenshot
    body = build_pr_body([rec], target_branch="vibe-niuma/dev", base_branch="main")
    assert "📷 截图" in body
    assert "https://example.com/s/2.png" in body


def test_pr_body_newest_first():
    records = [_make_record(1), _make_record(2), _make_record(3)]
    body = build_pr_body(records, target_branch="vibe-niuma/dev", base_branch="main")
    # cr-003 timestamp 是 2026-05-04，最新 → 应排第一
    pos_3 = body.find("#cr-003")
    pos_2 = body.find("#cr-002")
    pos_1 = body.find("#cr-001")
    assert 0 < pos_3 < pos_2 < pos_1


def test_pr_body_folds_older_records_beyond_window():
    # 25 条 > 默认窗口 20 → 5 条应被折叠
    records = [_make_record(i) for i in range(1, 26)]
    body = build_pr_body(
        records,
        target_branch="vibe-niuma/dev",
        base_branch="main",
        window=20,
    )
    assert "<details>" in body
    assert "更早 5 条已折叠" in body


def test_pr_body_no_fold_when_under_window():
    records = [_make_record(i) for i in range(1, 6)]  # 5 条 < 20
    body = build_pr_body(records, target_branch="vibe-niuma/dev", base_branch="main", window=20)
    assert "<details>" not in body


def test_pr_body_custom_window():
    records = [_make_record(i) for i in range(1, 11)]  # 10 条
    body = build_pr_body(records, target_branch="vibe-niuma/dev", base_branch="main", window=3)
    assert "更早 7 条已折叠" in body


def test_pr_body_truncates_long_business_text():
    long_text = "X" * 2000
    rec = CRRecord(
        cr_id="cr-long",
        business_text=long_text,
        commit_sha="abcdef1234",
        timestamp_iso="2026-05-18T12:00:00",
    )
    body = build_pr_body([rec], target_branch="vibe-niuma/dev", base_branch="main")
    assert "..." in body
    assert "X" * 2000 not in body  # 完整原文不出现


def test_pr_body_respects_max_chars_limit():
    """超量 CRs + 超长文本 → body 砍到 60k 以下。"""
    huge_text = "Y" * 400
    records = [
        CRRecord(
            cr_id=f"cr-{i}",
            business_text=huge_text,
            commit_sha=f"sha{i:08d}",
            timestamp_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00",
        )
        for i in range(200)
    ]
    body = build_pr_body(records, target_branch="vibe-niuma/dev", base_branch="main", window=200)
    assert len(body) <= MAX_PR_BODY_CHARS


# ── maintain_pr ────────────────────────────────────────────────────


@dataclass
class _CapturedUpsert:
    owner: str
    repo: str
    head_branch: str
    base_branch: str
    title: str
    body: str


class _FakeAPI:
    """简易 mock：记录 upsert_pr 调用，可控返回 created flag。"""
    def __init__(self, *, created: bool = True, pr_number: int = 42) -> None:
        self.captured: Optional[_CapturedUpsert] = None
        self._created = created
        self._pr_number = pr_number

    async def upsert_pr(
        self, owner: str, repo: str, *,
        head_branch: str, base_branch: str, title: str, body: str,
    ) -> tuple[dict, bool]:
        self.captured = _CapturedUpsert(
            owner=owner, repo=repo,
            head_branch=head_branch, base_branch=base_branch,
            title=title, body=body,
        )
        return ({"number": self._pr_number, "html_url": f"https://github.com/{owner}/{repo}/pull/{self._pr_number}"},
                self._created)


@pytest.mark.asyncio
async def test_maintain_pr_passes_correct_branches_to_upsert():
    api = _FakeAPI(created=True)
    records = [_make_record(1)]
    pr, created = await maintain_pr(
        api,  # type: ignore[arg-type]
        owner="weizhanhao", repo="vibe-niuma",
        target_branch="vibe-niuma/dev", base_branch="main",
        records=records,
    )
    assert created is True
    assert pr["number"] == 42
    assert api.captured is not None
    assert api.captured.owner == "weizhanhao"
    assert api.captured.repo == "vibe-niuma"
    assert api.captured.head_branch == "vibe-niuma/dev"
    assert api.captured.base_branch == "main"
    # title 含条数
    assert "1 条改动" in api.captured.title
    # body 含 cr_id
    assert "#cr-001" in api.captured.body


@pytest.mark.asyncio
async def test_maintain_pr_returns_false_when_updating_existing():
    api = _FakeAPI(created=False, pr_number=99)
    records = [_make_record(1), _make_record(2)]
    pr, created = await maintain_pr(
        api,  # type: ignore[arg-type]
        owner="o", repo="r",
        target_branch="vibe-niuma/dev", base_branch="main",
        records=records,
    )
    assert created is False
    assert pr["number"] == 99


@pytest.mark.asyncio
async def test_maintain_pr_propagates_not_found_error():
    from orchestrator.github_client import NotFoundError

    class _FailingAPI:
        async def upsert_pr(self, *a, **kw):
            raise NotFoundError("repo 不存在")

    with pytest.raises(NotFoundError, match="不存在"):
        await maintain_pr(
            _FailingAPI(),  # type: ignore[arg-type]
            owner="o", repo="missing",
            target_branch="vibe-niuma/dev", base_branch="main",
            records=[_make_record(1)],
        )
