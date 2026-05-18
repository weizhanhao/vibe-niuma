"""真实 E2E 冒烟（默认 skip）。

设 `VIBE_NIUMA_E2E=1` 才跑。它会用真实 Orchestrator 装配（claude/opencode + Docker
+ 真实 LLM 经代理）对 demo 跑一条已知简单改动，断言闭环到 `merged`。

环境前提（参见 docs/superpowers/plans 里 Plan 5 的清单）：
  - MySQL `demo-mysql` 已启 + `orchestrator` 库已建
  - Docker daemon 可用
  - claude 或 opencode CLI 已装
  - ANTHROPIC_BASE_URL 指向 LLM 代理（或 opencode 直连）
  - LLM_API_KEY 有效
  - DEMO_REPO_PATH 指向有 `main` 分支的 demo 副本
"""
from __future__ import annotations

import os
import time

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("VIBE_NIUMA_E2E"),
    reason="真实 E2E：需 VIBE_NIUMA_E2E=1 + Docker + LLM key",
)


def _payload(text: str) -> dict:
    return {
        "url": "http://demo.local/settings",
        "screenshot_b64": "x",
        "box_coords": {"x": 0, "y": 0, "width": 800, "height": 600},
        "viewport": {"width": 1280, "height": 800},
        "request_text": text,
    }


def _wait(client, rid, target, timeout=600.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/change-requests/{rid}").json()["state"]
        if last == target:
            return
        if last == "failed":
            detail = client.get(f"/change-requests/{rid}").json()
            raise AssertionError(f"failed: {detail}")
        time.sleep(2.0)
    raise AssertionError(f"{rid} 卡在 {last}，未到 {target}（{timeout}s 超时）")


@pytest.mark.e2e
def test_real_e2e_simple_text_change_to_merged():
    """对 demo 跑一条简单改动 → preview-ready → 合并 → 改动进 main。"""
    from fastapi.testclient import TestClient
    from orchestrator.main import app

    text = "把 /settings 页面的「保存」按钮文案改成「立即保存」"
    with TestClient(app) as client:
        rid = client.post("/change-requests", json=_payload(text)).json()["id"]
        _wait(client, rid, "preview-ready", timeout=600.0)
        merged = client.post(f"/change-requests/{rid}/merge").json()
        assert merged["state"] == "merged"
