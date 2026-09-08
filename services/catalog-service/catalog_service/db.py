import asyncpg

from app_common.models import MenuItem

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS menu_items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT,
    price NUMERIC NOT NULL
);
"""

SEED_ITEMS = [
    ("margherita", "Margherita", "Classic tomato, mozzarella, basil", "classic", 9.50),
    ("pepperoni", "Pepperoni", "Tomato, mozzarella, pepperoni", "classic", 11.00),
    ("hawaiian", "Hawaiian", "Ham, pineapple, mozzarella", "classic", 10.00),
    ("quattro-formaggi", "Quattro Formaggi", "Four cheese blend", "specialty", 12.50),
    ("diavola", "Diavola", "Spicy salami, chili, mozzarella", "specialty", 11.50),
    ("vegetariana", "Vegetariana", "Grilled vegetables, mozzarella", "vegetarian", 10.50),
]


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE)
        await conn.executemany(
            """
            INSERT INTO menu_items (id, name, description, category, price)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO NOTHING
            """,
            SEED_ITEMS,
        )


async def list_menu_items(pool: asyncpg.Pool) -> list[MenuItem]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, description, category, price FROM menu_items ORDER BY name"
        )
    return [_row_to_item(row) for row in rows]


async def get_menu_item(pool: asyncpg.Pool, item_id: str) -> MenuItem | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, description, category, price FROM menu_items WHERE id = $1",
            item_id,
        )
    return _row_to_item(row) if row else None


def _row_to_item(row) -> MenuItem:
    return MenuItem(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        category=row["category"],
        price=float(row["price"]),
    )
