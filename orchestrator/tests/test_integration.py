"""端到端：用 fake adapter 把一条变更请求从 created 一路驱动到 merged，
覆盖完整 FSM + REST + SSE 事件 + git 合并。

SSE 部分对 `app_state.event_bus.history()` 断言 —— 那是 SSE 端点
（`subscribe` 回放的 buffer）的同一份数据源；不走 TestClient 同步流式读，
避免无限 SSE 生成器在测试里死锁。
"""
import time


def _payload(text="把订单列表的标题改大一号"):
    return {
        "url": "http://x/orders",
        "screenshot_b64": "img",
        "box_coords": {"x": 10, "y": 20, "width": 100, "height": 40},
        "viewport": {"width": 1440, "height": 900},
        "request_text": text,
    }


def _wait(client, rid, target, timeout=8.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/change-requests/{rid}").json()["state"]
        if last == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"{rid} 卡在 {last}，未到 {target}")


def test_full_lifecycle_created_to_merged(client, orchestrator_repo):
    from orchestrator.main import app_state

    # 1. 创建变更请求
    rid = client.post("/change-requests", json=_payload()).json()["id"]

    # 2. 流水线（fake adapter）自动推进到 preview-ready
    _wait(client, rid, "preview-ready")
    detail = client.get(f"/change-requests/{rid}").json()
    assert detail["branch"] == f"cr/{rid}"
    assert detail["preview_url"]

    # 3. SSE 事件历史包含完整状态轨迹
    events = app_state.event_bus.history(rid)
    states = [e.data.get("state") for e in events if e.type == "status"]
    for expected in ("clarifying", "located", "coding", "building", "preview-ready"):
        assert expected in states, f"SSE 缺少状态 {expected}"

    # 4. 业务员确认合并
    merged = client.post(f"/change-requests/{rid}/merge").json()
    assert merged["state"] == "merged"


def test_full_lifecycle_with_discard(client):
    rid = client.post("/change-requests", json=_payload("丢弃这条")).json()["id"]
    _wait(client, rid, "preview-ready")
    discarded = client.post(f"/change-requests/{rid}/discard").json()
    assert discarded["state"] == "discarded"
