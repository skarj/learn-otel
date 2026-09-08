import json
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis_asyncio
from fastapi import FastAPI, HTTPException

from otel_common.logging import configure_logging
from otel_common.models import MenuItem
from otel_common.retry import retry_async

from catalog_service import db
from catalog_service.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)

MENU_CACHE_KEY = "menu:all"
MENU_CACHE_TTL_SECONDS = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await retry_async(
        lambda: asyncpg.create_pool(settings.postgres_dsn), what="postgres"
    )
    await db.init_db(app.state.pool)

    app.state.redis = redis_asyncio.from_url(settings.redis_url)
    await retry_async(lambda: app.state.redis.ping(), what="redis")

    logger.info("catalog-service ready")
    yield
    await app.state.redis.aclose()
    await app.state.pool.close()


app = FastAPI(title="catalog-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/menu", response_model=list[MenuItem])
async def get_menu():
    cached = await app.state.redis.get(MENU_CACHE_KEY)
    if cached is not None:
        return [MenuItem(**item) for item in json.loads(cached)]

    items = await db.list_menu_items(app.state.pool)
    await app.state.redis.set(
        MENU_CACHE_KEY,
        json.dumps([item.model_dump() for item in items]),
        ex=MENU_CACHE_TTL_SECONDS,
    )
    return items


@app.get("/menu/{item_id}", response_model=MenuItem)
async def get_menu_item(item_id: str):
    item = await db.get_menu_item(app.state.pool, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="menu item not found")
    return item
