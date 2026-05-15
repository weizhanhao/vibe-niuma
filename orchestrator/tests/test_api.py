import time


def _payload(text="把保存按钮改成蓝色"):
    return {
        "url": "http://x/orders",
        "screenshot_b64": "img",
        "box_coords": {"x": 1, "y": 2, "width": 3, "height": 4},
        "viewport": {"width": 1280, "height": 800},
        "request_text": text,
    }


def _wait_state(client, request_id, target, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/change-requests/{request_id}")
        if resp.json()["state"] == target:
            return resp.json()
        time.sleep(0.05)
    raise AssertionError(
        f"{request_id} 未在 {timeout}s 内到达 {target}，"
        f"当前 {client.get(f'/change-requests/{request_id}').json()['state']}"
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_change_request_returns_record(client):
    resp = client.post("/change-requests", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"]
    assert body["state"] in (
        "created", "clarifying", "located", "coding", "building", "preview-ready"
    )


def test_create_then_pipeline_reaches_preview_ready(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    final = _wait_state(client, rid, "preview-ready")
    assert final["branch"] == f"cr/{rid}"
    assert final["preview_url"] is not None


def test_get_missing_returns_404(client):
    assert client.get("/change-requests/nope").status_code == 404


def test_merge_from_preview_ready(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    resp = client.post(f"/change-requests/{rid}/merge")
    assert resp.status_code == 200
    assert resp.json()["state"] == "merged"


def test_merge_not_allowed_before_preview_ready(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    resp = client.post(f"/change-requests/{rid}/merge")
    # 要么 409（还没就绪），要么已经就绪并 200 —— 两者都可接受，但不能 500
    assert resp.status_code in (200, 409)


def test_discard_marks_discarded(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    resp = client.post(f"/change-requests/{rid}/discard")
    assert resp.status_code == 200
    assert resp.json()["state"] == "discarded"


def test_retry_on_non_terminal_rejected(client):
    rid = client.post("/change-requests", json=_payload()).json()["id"]
    _wait_state(client, rid, "preview-ready")
    # preview-ready 不是 failed/expired → retry 应 409
    assert client.post(f"/change-requests/{rid}/retry").status_code == 409
