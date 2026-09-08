from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class OrderStatus(StrEnum):
    CREATED = "created"
    PRICED = "priced"
    COOKING = "cooking"
    READY = "ready"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    FAILED = "failed"


class MenuItem(BaseModel):
    id: str
    name: str
    description: str
    category: str
    price: float


class OrderItemRequest(BaseModel):
    pizza_id: str
    quantity: int = Field(ge=1, le=10)


class OrderCreateRequest(BaseModel):
    """Public request shape (client only knows pizza ids, not prices)."""

    items: list[OrderItemRequest]
    promo_code: str | None = None


class OrderItem(BaseModel):
    pizza_id: str
    name: str
    quantity: int
    unit_price: float


class OrderCreateInternalRequest(BaseModel):
    """frontend-gateway -> order-service, items already resolved against the catalog."""

    items: list[OrderItem]
    promo_code: str | None = None


class Order(BaseModel):
    id: str
    status: OrderStatus
    items: list[OrderItem]
    total_price: float
    promo_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PricingRequest(BaseModel):
    items: list[OrderItem]
    promo_code: str | None = None


class PricingResponse(BaseModel):
    total_price: float
    discount_applied: float


class StatusUpdateRequest(BaseModel):
    status: OrderStatus


# NATS event payloads. `order_id` on every event keeps consumers simple.
class OrderCreatedEvent(BaseModel):
    order_id: str
    items: list[OrderItem]
    total_price: float


class OrderReadyEvent(BaseModel):
    order_id: str


class OrderDeliveredEvent(BaseModel):
    order_id: str
