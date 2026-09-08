import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]], *, retries: int = 30, delay: float = 2.0, what: str = "dependency"
) -> T:
    """Retry an async startup call (DB pool, broker connect, ...) so a pod doesn't
    crash-loop for the ~10-20s it takes other Deployments to become ready."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.info("waiting for %s (attempt %d/%d): %s", what, attempt + 1, retries, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"{what} never became available") from last_exc
