from demo_backend.models import AppSetting, Order, OrderItem


def test_order_has_expected_columns():
    cols = set(Order.__table__.columns.keys())
    assert cols == {"id", "customer_name", "status", "total_amount", "created_at"}


def test_order_item_has_expected_columns():
    cols = set(OrderItem.__table__.columns.keys())
    assert cols == {"id", "order_id", "product_name", "quantity", "unit_price"}


def test_app_setting_has_expected_columns():
    cols = set(AppSetting.__table__.columns.keys())
    assert cols == {"key", "value"}
