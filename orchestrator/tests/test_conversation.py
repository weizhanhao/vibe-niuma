"""Plan 9 Task 1+2: Conversation model + repository + REST endpoints。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator.conversation import ConversationRepository
from orchestrator.models import ChangeRequest


# ── model + repo ───────────────────────────────────────────────────
def test_create_conversation_with_empty_messages(db_session):
    repo = ConversationRepository(db_session)
    conv = repo.create()
    assert conv.id and len(conv.id) >= 8
    assert conv.title == ""
    assert conv.messages == []
    assert conv.archived_at is None


def test_append_message_persists_in_order(db_session):
    repo = ConversationRepository(db_session)
    conv = repo.create()
    repo.append_message(conv.id, {"type": "user", "ts": "t1", "content": "改订单徽章"})
    repo.append_message(conv.id, {"type": "ai", "ts": "t2", "content": "好"})
    refreshed = repo.get(conv.id)
    assert refreshed is not None
    assert len(refreshed.messages) == 2
    assert refreshed.messages[0]["type"] == "user"
    assert refreshed.messages[1]["type"] == "ai"


def test_first_user_message_becomes_title(db_session):
    repo = ConversationRepository(db_session)
    conv = repo.create()
    repo.append_message(conv.id, {"type": "user", "ts": "t1", "content": "改订单徽章颜色"})
    refreshed = repo.get(conv.id)
    assert refreshed.title == "改订单徽章颜色"


def test_archive_sets_archived_at(db_session):
    repo = ConversationRepository(db_session)
    conv = repo.create()
    repo.archive(conv.id)
    refreshed = repo.get(conv.id)
    assert refreshed.archived_at is not None


def test_list_active_excludes_archived(db_session):
    repo = ConversationRepository(db_session)
    a = repo.create()
    b = repo.create()
    repo.archive(a.id)
    active = repo.list_active()
    active_ids = {c.id for c in active}
    assert b.id in active_ids
    assert a.id not in active_ids


# bug: prod 上 GET /conversations?archived=false 500 —— messages JSON 列累积大后
# ORDER BY updated_at DESC 撞 MySQL sort_buffer_size。list_meta 跳过 messages
# 字段拿元数据，前端 tab 命名 + 历史列表都靠这条。
def test_list_meta_omits_messages_payload(db_session):
    repo = ConversationRepository(db_session)
    conv = repo.create()
    # 灌一条很长的 message，模拟生产上累积的 LLM 历史
    big = "x" * 10_000
    repo.append_message(conv.id, {
        "id": "m1", "type": "user", "ts": "2026-05-17T20:00:00", "content": big,
    })
    items = repo.list_meta()
    assert len(items) >= 1
    me = next(i for i in items if i["id"] == conv.id)
    # messages 字段必须空（list 接口不传 payload）
    assert me["messages"] == []
    # 但元数据全在
    assert me["title"].startswith("xxxx")
    assert me["updated_at"] is not None
    assert me["created_at"] is not None


def test_list_meta_excludes_archived_by_default(db_session):
    repo = ConversationRepository(db_session)
    a = repo.create()
    b = repo.create()
    repo.archive(a.id)
    ids = {i["id"] for i in repo.list_meta()}
    assert b.id in ids and a.id not in ids
    ids_all = {i["id"] for i in repo.list_meta(include_archived=True)}
    assert a.id in ids_all and b.id in ids_all


def test_change_request_conversation_id_fk(db_session):
    repo = ConversationRepository(db_session)
    conv = repo.create()
    cr = ChangeRequest(
        id="cr-test-1",
        url="http://x", screenshot_b64="",
        box_coords={}, viewport={},
        request_text="t", state="created",
        conversation_id=conv.id,
    )
    db_session.add(cr)
    db_session.commit()
    refreshed = db_session.get(ChangeRequest, "cr-test-1")
    assert refreshed.conversation_id == conv.id


# ── REST ───────────────────────────────────────────────────────────
def test_post_conversation_creates_and_returns_id(client: TestClient):
    r = client.post("/conversations", json={"title": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] and body["title"] == "test"
    assert body["messages"] == []


def test_get_conversations_lists_active_only(client: TestClient):
    a = client.post("/conversations", json={}).json()
    b = client.post("/conversations", json={}).json()
    client.post(f"/conversations/{a['id']}/archive")
    items = client.get("/conversations?archived=false").json()["items"]
    ids = {c["id"] for c in items}
    assert b["id"] in ids
    assert a["id"] not in ids


@pytest.mark.skip(reason="[Plan 10 obsolete] 老的 raw-append 入口替换为 POST /messages "
                         "({text, attachments?, override_mode?}) + 意图路由；端到端覆盖见 "
                         "test_messages_api.py")
def test_post_message_appends(client: TestClient):
    conv = client.post("/conversations", json={}).json()
    r = client.post(
        f"/conversations/{conv['id']}/messages",
        json={"type": "user", "ts": "t1", "content": "hi"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "hi"


def test_get_nonexistent_conversation_returns_404(client: TestClient):
    r = client.get("/conversations/nonexistent")
    assert r.status_code == 404
