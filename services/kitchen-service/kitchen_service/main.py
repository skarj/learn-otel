import asyncio
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI

from otel_common.http import create_http_client
from otel_common.logging import configure_logging
from otel_common.messaging import connect, consume_forever, ensure_stream
from otel_common.models import OrderCreatedEvent, OrderStatus
from otel_common.retry import retry_async

from kitchen_service.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)

STREAM_NAME = "orders"
STREAM_SUBJECTS = ["order.created", "order.ready", "order.delivered"]

_http = None


async def handle_order_created(data: bytes) -> None:
    event = OrderCreatedEvent.model_validate_json(data)
    logger.info("cooking order %s", event.order_id)

    await _http.patch(f"/orders/{event.order_id}/status", json={"status": OrderStatus.COOKING.value})
    await asyncio.sleep(random.uniform(2, 6))
    await _http.patch(f"/orders/{event.order_id}/status", json={"status": OrderStatus.READY.value})

    logger.info("order %s ready", event.order_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http
    app.state.nc, app.state.js = await retry_async(lambda: connect(settings.nats_url), what="nats")
    await ensure_stream(app.state.js, STREAM_NAME, STREAM_SUBJECTS)
    _http = create_http_client(settings.order_service_url)
    app.state.task = asyncio.create_task(
        consume_forever(app.state.js, "order.created", "kitchen-order-created", handle_order_created)
    )
    logger.info("kitchen-service ready")
    yield
    app.state.task.cancel()
    await _http.aclose()
    await app.state.nc.close()


app = FastAPI(title="kitchen-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
