import logging
from collections.abc import Awaitable, Callable

import nats
from nats.js import JetStreamContext

logger = logging.getLogger(__name__)


async def connect(url: str) -> tuple[nats.NATS, JetStreamContext]:
    nc = await nats.connect(url)
    return nc, nc.jetstream()


async def ensure_stream(js: JetStreamContext, name: str, subjects: list[str]) -> None:
    try:
        await js.add_stream(name=name, subjects=subjects)
    except Exception as exc:  # noqa: BLE001 - stream-already-exists is not distinguishable cheaply
        if "already" not in str(exc).lower() and "in use" not in str(exc).lower():
            raise


async def publish_event(js: JetStreamContext, subject: str, payload) -> None:
    await js.publish(subject, payload.model_dump_json().encode())


async def consume_forever(
    js: JetStreamContext,
    subject: str,
    durable: str,
    handler: Callable[[bytes], Awaitable[None]],
) -> None:
    """Pull-based durable consumer. Each unique `durable` name gets its own
    independent copy of every message on `subject` - this is how multiple
    services (e.g. kitchen-service and notification-service) both consume
    the same event without stealing messages from each other."""
    sub = await js.pull_subscribe(subject, durable=durable)
    while True:
        try:
            msgs = await sub.fetch(1, timeout=5)
        except nats.errors.TimeoutError:
            continue
        for msg in msgs:
            try:
                await handler(msg.data)
                await msg.ack()
            except Exception:
                logger.exception("handler failed for subject=%s durable=%s", subject, durable)
                await msg.nak()
