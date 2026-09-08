from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg
from fastapi import FastAPI, HTTPException

from app_common.http import create_http_client
from app_common.logging import configure_logging
from app_common.messaging import connect, ensure_stream, publish_event
from app_common.retry import retry_async
from app_common.models import (
    Order,
    OrderCreatedEvent,
    OrderCreateInternalRequest,
    OrderDeliveredEvent,
    OrderReadyEvent,
    OrderStatus,
    PricingRequest,
    PricingResponse,
    StatusUpdateRequest,
)

from order_service import db
from order_service.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)

STREAM_NAME = "orders"
STREAM_SUBJECTS = ["order.created", "order.ready", "order.delivered"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await retry_async(
        lambda: asyncpg.create_pool(settings.postgres_dsn), what="postgres"
    )
    await db.init_db(app.state.pool)
    app.state.nc, app.state.js = await retry_async(lambda: connect(settings.nats_url), what="nats")
    await ensure_stream(app.state.js, STREAM_NAME, STREAM_SUBJECTS)
    app.state.pricing_http = create_http_client(settings.pricing_service_url)
    logger.info("order-service ready")
    yield
    await app.state.pricing_http.aclose()
    await app.state.nc.close()
    await app.state.pool.close()


app = FastAPI(title="order-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/orders", response_model=Order)
async def create_order(payload: OrderCreateInternalRequest):
    order = await db.create_order(app.state.pool, payload.items, payload.promo_code)

    pricing_request = PricingRequest(items=payload.items, promo_code=payload.promo_code)
    resp = await app.state.pricing_http.post("/calculate", json=pricing_request.model_dump())
    resp.raise_for_status()
    pricing = PricingResponse(**resp.json())

    await db.set_price(app.state.pool, UUID(order.id), pricing.total_price)

    await publish_event(
        app.state.js,
        "order.created",
        OrderCreatedEvent(order_id=order.id, items=payload.items, total_price=pricing.total_price),
    )

    order.total_price = pricing.total_price
    order.status = OrderStatus.PRICED
    return order


@app.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: UUID):
    order = await db.get_order(app.state.pool, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.patch("/orders/{order_id}/status")
async def update_status(order_id: UUID, payload: StatusUpdateRequest):
    order = await db.get_order(app.state.pool, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    await db.set_status(app.state.pool, order_id, payload.status)

    if payload.status == OrderStatus.READY:
        await publish_event(app.state.js, "order.ready", OrderReadyEvent(order_id=str(order_id)))
    elif payload.status == OrderStatus.DELIVERED:
        await publish_event(
            app.state.js, "order.delivered", OrderDeliveredEvent(order_id=str(order_id))
        )
    return {"status": "updated"}
