import json
from uuid import uuid4

import asyncpg

from otel_common.models import Order, OrderItem, OrderStatus

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL,
    items JSONB NOT NULL,
    total_price NUMERIC,
    promo_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE)


async def create_order(
    pool: asyncpg.Pool, items: list[OrderItem], promo_code: str | None
) -> Order:
    order_id = uuid4()
    items_json = json.dumps([item.model_dump() for item in items])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (id, status, items, promo_code)
            VALUES ($1, $2, $3, $4)
            RETURNING id, status, items, total_price, promo_code, created_at
            """,
            order_id,
            OrderStatus.CREATED.value,
            items_json,
            promo_code,
        )
    return _row_to_order(row)


async def set_price(pool: asyncpg.Pool, order_id, total_price: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET total_price = $1, status = $2 WHERE id = $3",
            total_price,
            OrderStatus.PRICED.value,
            order_id,
        )


async def set_status(pool: asyncpg.Pool, order_id, status: OrderStatus) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orders SET status = $1 WHERE id = $2", status.value, order_id)


async def get_order(pool: asyncpg.Pool, order_id) -> Order | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, items, total_price, promo_code, created_at FROM orders WHERE id = $1",
            order_id,
        )
    return _row_to_order(row) if row else None


def _row_to_order(row) -> Order:
    return Order(
        id=str(row["id"]),
        status=OrderStatus(row["status"]),
        items=[OrderItem(**item) for item in json.loads(row["items"])],
        total_price=float(row["total_price"]) if row["total_price"] is not None else 0.0,
        promo_code=row["promo_code"],
        created_at=row["created_at"],
    )
