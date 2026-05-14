from demo_backend.models import Order, OrderItem


def _make_order(db_session, customer_name="张三", status="paid"):
    order = Order(customer_name=customer_name, status=status, total_amount=100.0)
    order.items = [
        OrderItem(product_name="键盘", quantity=1, unit_price=60.0),
        OrderItem(product_name="鼠标", quantity=2, unit_price=20.0),
    ]
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_list_orders_returns_summaries(client, db_session):
    _make_order(db_session, customer_name="张三")
    _make_order(db_session, customer_name="李四")

    resp = client.get("/api/orders")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {o["customer_name"] for o in body} == {"张三", "李四"}
    assert "items" not in body[0]


def test_get_order_detail_includes_items(client, db_session):
    order = _make_order(db_session)

    resp = client.get(f"/api/orders/{order.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_name"] == "张三"
    assert len(body["items"]) == 2
    assert {i["product_name"] for i in body["items"]} == {"键盘", "鼠标"}


def test_get_order_detail_404_when_missing(client, db_session):
    resp = client.get("/api/orders/99999")
    assert resp.status_code == 404
