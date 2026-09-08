# Build context is the repo root: docker build -f services/delivery-service/Dockerfile .
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv==0.6.6
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/delivery-service ./services/delivery-service
RUN uv sync --frozen --package delivery-service --no-dev

FROM python:3.12-slim
RUN useradd -u 1000 -m appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/delivery-service/delivery_service ./delivery_service
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "delivery_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
