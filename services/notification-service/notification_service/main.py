import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app_common.logging import configure_logging
from app_common.messaging import connect, consume_forever, ensure_stream
from app_common.models import OrderCreatedEvent, OrderDeliveredEvent, OrderReadyEvent
from app_common.retry import retry_async

from notification_service.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)

STREAM_NAME = "orders"
STREAM_SUBJECTS = ["order.created", "order.ready", "order.delivered"]


async def handle_created(data: bytes) -> None:
    event = OrderCreatedEvent.model_validate_json(data)
    logger.info("notification: order %s created", event.order_id)


async def handle_ready(data: bytes) -> None:
    event = OrderReadyEvent.model_validate_json(data)
    logger.info("notification: order %s is ready", event.order_id)


async def handle_delivered(data: bytes) -> None:
    event = OrderDeliveredEvent.model_validate_json(data)
    logger.info("notification: order %s delivered, enjoy!", event.order_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.nc, app.state.js = await retry_async(lambda: connect(settings.nats_url), what="nats")
    await ensure_stream(app.state.js, STREAM_NAME, STREAM_SUBJECTS)
    app.state.tasks = [
        asyncio.create_task(
            consume_forever(app.state.js, "order.created", "notification-order-created", handle_created)
        ),
        asyncio.create_task(
            consume_forever(app.state.js, "order.ready", "notification-order-ready", handle_ready)
        ),
        asyncio.create_task(
            consume_forever(
                app.state.js, "order.delivered", "notification-order-delivered", handle_delivered
            )
        ),
    ]
    logger.info("notification-service ready")
    yield
    for task in app.state.tasks:
        task.cancel()
    await app.state.nc.close()


app = FastAPI(title="notification-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
