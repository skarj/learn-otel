import asyncio
import random

from fastapi import FastAPI, HTTPException

from otel_common.logging import configure_logging
from otel_common.models import PricingRequest, PricingResponse

from pricing_service.settings import settings

logger = configure_logging(settings.service_name, settings.log_level)

app = FastAPI(title="pricing-service")

PROMO_DISCOUNT_RATE = 0.10
SLOW_DOWNSTREAM_PROBABILITY = 0.08
FAILING_DOWNSTREAM_PROBABILITY = 0.025


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/calculate", response_model=PricingResponse)
async def calculate(payload: PricingRequest):
    await asyncio.sleep(random.uniform(0.05, 0.15))

    if random.random() < FAILING_DOWNSTREAM_PROBABILITY:
        raise HTTPException(status_code=503, detail="tax rate service unavailable")

    if random.random() < SLOW_DOWNSTREAM_PROBABILITY:
        await asyncio.sleep(random.uniform(1.5, 3.0))

    subtotal = sum(item.unit_price * item.quantity for item in payload.items)
    discount = subtotal * PROMO_DISCOUNT_RATE if payload.promo_code == "PIZZA10" else 0.0

    return PricingResponse(total_price=round(subtotal - discount, 2), discount_applied=round(discount, 2))
