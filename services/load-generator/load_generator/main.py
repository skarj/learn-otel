import asyncio
import os
import random

import httpx

from otel_common.logging import configure_logging

TARGET_URL = os.environ.get("TARGET_URL", "http://frontend-gateway:8000")
MIN_INTERVAL = float(os.environ.get("MIN_INTERVAL", "2.0"))
MAX_INTERVAL = float(os.environ.get("MAX_INTERVAL", "3.5"))
BURST_PROBABILITY = float(os.environ.get("BURST_PROBABILITY", "0.02"))

logger = configure_logging("load-generator")

PROMO_CODES = [None, None, None, "PIZZA10"]
recent_order_ids: list[str] = []


async def fetch_menu(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get("/menu")
    resp.raise_for_status()
    return resp.json()


async def wait_for_menu(client: httpx.AsyncClient, retries: int = 30, delay: float = 2.0) -> list[dict]:
    for _ in range(retries):
        try:
            return await fetch_menu(client)
        except Exception as exc:
            logger.info("waiting for frontend-gateway to be ready: %s", exc)
            await asyncio.sleep(delay)
    raise RuntimeError("frontend-gateway never became ready")


async def place_order(client: httpx.AsyncClient, menu: list[dict]) -> None:
    items = random.sample(menu, k=random.randint(1, min(4, len(menu))))
    payload = {
        "items": [{"pizza_id": item["id"], "quantity": random.randint(1, 3)} for item in items],
        "promo_code": random.choice(PROMO_CODES),
    }
    resp = await client.post("/orders", json=payload)
    if resp.status_code < 300:
        order = resp.json()
        recent_order_ids.append(order["id"])
        if len(recent_order_ids) > 50:
            recent_order_ids.pop(0)
        logger.info("placed order %s", order["id"])
    else:
        logger.warning("order failed: %s %s", resp.status_code, resp.text)


async def get_order_status(client: httpx.AsyncClient) -> None:
    if not recent_order_ids:
        return
    order_id = random.choice(recent_order_ids)
    resp = await client.get(f"/orders/{order_id}")
    logger.info("order %s status check -> %s", order_id, resp.status_code)


async def place_malformed_order(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/orders", json={"items": [{"pizza_id": "does-not-exist", "quantity": 1}]}
    )
    logger.info("malformed order -> %s", resp.status_code)


async def one_iteration(client: httpx.AsyncClient, menu: list[dict]) -> None:
    roll = random.random()
    if roll < 0.60:
        await fetch_menu(client)
    elif roll < 0.90:
        await place_order(client, menu)
    elif roll < 0.95:
        await get_order_status(client)
    else:
        await place_malformed_order(client)


async def main() -> None:
    async with httpx.AsyncClient(base_url=TARGET_URL, timeout=10.0) as client:
        menu = await wait_for_menu(client)
        logger.info("loaded menu with %d items, starting traffic loop", len(menu))
        loop = asyncio.get_event_loop()
        while True:
            if random.random() < BURST_PROBABILITY:
                logger.info("burst starting")
                burst_end = loop.time() + random.uniform(10, 20)
                while loop.time() < burst_end:
                    await one_iteration(client, menu)
                    await asyncio.sleep(random.uniform(MIN_INTERVAL, MAX_INTERVAL) / 5)
            else:
                await one_iteration(client, menu)
            await asyncio.sleep(random.uniform(MIN_INTERVAL, MAX_INTERVAL))


if __name__ == "__main__":
    asyncio.run(main())
