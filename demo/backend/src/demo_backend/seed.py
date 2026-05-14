from sqlalchemy.orm import Session

from demo_backend.models import AppSetting, Order, OrderItem

_ORDERS = [
    ("张三", "paid", [("机械键盘", 1, 299.0), ("无线鼠标", 1, 99.0)]),
    ("李四", "pending", [("27寸显示器", 2, 1299.0)]),
    ("王五", "shipped", [("USB-C 扩展坞", 1, 359.0), ("HDMI 线", 3, 29.0)]),
    ("赵六", "paid", [("人体工学椅", 1, 1599.0)]),
    ("钱七", "cancelled", [("笔记本支架", 1, 159.0)]),
]

_SETTINGS = {
    "page_title": "订单管理",
    "rows_per_page": "20",
    "currency_symbol": "¥",
}


def seed_if_empty(db: Session) -> None:
    if db.query(Order).count() == 0:
        for customer_name, status, items in _ORDERS:
            order_items = [
                OrderItem(product_name=p, quantity=q, unit_price=u)
                for p, q, u in items
            ]
            total = sum(q * u for _, q, u in items)
            db.add(
                Order(
                    customer_name=customer_name,
                    status=status,
                    total_amount=total,
                    items=order_items,
                )
            )
    if db.query(AppSetting).count() == 0:
        for key, value in _SETTINGS.items():
            db.add(AppSetting(key=key, value=value))
    db.commit()
