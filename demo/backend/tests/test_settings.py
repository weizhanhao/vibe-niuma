from demo_backend.models import AppSetting


def test_list_settings_returns_all(client, db_session):
    db_session.add(AppSetting(key="page_title", value="订单管理"))
    db_session.add(AppSetting(key="rows_per_page", value="20"))
    db_session.commit()

    resp = client.get("/api/settings")

    assert resp.status_code == 200
    body = resp.json()
    assert {s["key"]: s["value"] for s in body} == {
        "page_title": "订单管理",
        "rows_per_page": "20",
    }


def test_update_setting_changes_value(client, db_session):
    db_session.add(AppSetting(key="page_title", value="订单管理"))
    db_session.commit()

    resp = client.put("/api/settings/page_title", json={"value": "订单中心"})

    assert resp.status_code == 200
    assert resp.json()["value"] == "订单中心"
    refreshed = client.get("/api/settings").json()
    assert {s["key"]: s["value"] for s in refreshed}["page_title"] == "订单中心"


def test_update_setting_404_when_missing(client, db_session):
    resp = client.put("/api/settings/nope", json={"value": "x"})
    assert resp.status_code == 404
