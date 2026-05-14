from pydantic import BaseModel, ConfigDict


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_name: str
    quantity: int
    unit_price: float


class OrderSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    status: str
    total_amount: float


class OrderDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    status: str
    total_amount: float
    items: list[OrderItemOut]


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str


class SettingUpdateIn(BaseModel):
    value: str
