from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app_common.http import create_http_client
from app_common.logging import configure_logging
from app_common.models import (
    MenuItem,
    OrderCreateInternalRequest,
    OrderCreateRequest,
    OrderItem,
)

from frontend_gateway.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.catalog_http = create_http_client(settings.catalog_service_url)
    app.state.order_http = create_http_client(settings.order_service_url)
    logger.info("frontend-gateway ready")
    yield
    await app.state.catalog_http.aclose()
    await app.state.order_http.aclose()


app = FastAPI(title="frontend-gateway", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/menu", response_model=list[MenuItem])
async def get_menu():
    resp = await app.state.catalog_http.get("/menu")
    resp.raise_for_status()
    return resp.json()


@app.post("/orders")
async def create_order(payload: OrderCreateRequest):
    menu_resp = await app.state.catalog_http.get("/menu")
    menu_resp.raise_for_status()
    menu_by_id = {item["id"]: item for item in menu_resp.json()}

    items: list[OrderItem] = []
    for requested in payload.items:
        menu_item = menu_by_id.get(requested.pizza_id)
        if menu_item is None:
            raise HTTPException(status_code=400, detail=f"unknown pizza_id {requested.pizza_id}")
        items.append(
            OrderItem(
                pizza_id=requested.pizza_id,
                name=menu_item["name"],
                quantity=requested.quantity,
                unit_price=menu_item["price"],
            )
        )

    order_request = OrderCreateInternalRequest(items=items, promo_code=payload.promo_code)
    resp = await app.state.order_http.post("/orders", json=order_request.model_dump())
    resp.raise_for_status()
    return resp.json()


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    resp = await app.state.order_http.get(f"/orders/{order_id}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Order not found")
    resp.raise_for_status()
    return resp.json()
