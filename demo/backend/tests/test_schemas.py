from demo_backend.schemas import OrderDetailOut, OrderItemOut, OrderSummaryOut, SettingOut


def test_order_summary_out_fields():
    s = OrderSummaryOut(
        id=1, customer_name="张三", status="paid", total_amount=99.5
    )
    assert s.id == 1
    assert s.customer_name == "张三"


def test_order_detail_out_includes_items():
    item = OrderItemOut(id=1, product_name="键盘", quantity=2, unit_price=50.0)
    d = OrderDetailOut(
        id=1, customer_name="张三", status="paid", total_amount=100.0, items=[item]
    )
    assert d.items[0].product_name == "键盘"


def test_setting_out_fields():
    s = SettingOut(key="page_title", value="订单管理")
    assert s.key == "page_title"
    assert s.value == "订单管理"
