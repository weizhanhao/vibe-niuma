from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from demo_backend.db import get_db
from demo_backend.models import Order
from demo_backend.schemas import OrderDetailOut, OrderSummaryOut

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=list[OrderSummaryOut])
def list_orders(db: Session = Depends(get_db)) -> list[Order]:
    return db.query(Order).order_by(Order.id).all()


@router.get("/{order_id}", response_model=OrderDetailOut)
def get_order(order_id: int, db: Session = Depends(get_db)) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order
