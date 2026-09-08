import asyncio
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app_common.http import create_http_client
from app_common.logging import configure_logging
from app_common.messaging import connect, consume_forever, ensure_stream
from app_common.models import OrderReadyEvent, OrderStatus
from app_common.retry import retry_async

from delivery_service.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)

STREAM_NAME = "orders"
STREAM_SUBJECTS = ["order.created", "order.ready", "order.delivered"]

_http = None


async def handle_order_ready(data: bytes) -> None:
    event = OrderReadyEvent.model_validate_json(data)
    logger.info("out for delivery: order %s", event.order_id)

    await _http.patch(
        f"/orders/{event.order_id}/status", json={"status": OrderStatus.OUT_FOR_DELIVERY.value}
    )
    await asyncio.sleep(random.uniform(3, 8))
    await _http.patch(f"/orders/{event.order_id}/status", json={"status": OrderStatus.DELIVERED.value})

    logger.info("order %s delivered", event.order_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http
    app.state.nc, app.state.js = await retry_async(lambda: connect(settings.nats_url), what="nats")
    await ensure_stream(app.state.js, STREAM_NAME, STREAM_SUBJECTS)
    _http = create_http_client(settings.order_service_url)
    app.state.task = asyncio.create_task(
        consume_forever(app.state.js, "order.ready", "delivery-order-ready", handle_order_ready)
    )
    logger.info("delivery-service ready")
    yield
    app.state.task.cancel()
    await _http.aclose()
    await app.state.nc.close()


app = FastAPI(title="delivery-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
