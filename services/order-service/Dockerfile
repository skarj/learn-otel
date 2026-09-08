# Build context is the repo root: docker build -f services/order-service/Dockerfile .
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv==0.6.6
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/order-service ./services/order-service
RUN uv sync --frozen --package order-service --no-dev

FROM python:3.12-slim
RUN useradd -u 1000 -m appuser
WORKDIR /app
COPY --from=builder /workspace/.venv /app/.venv
COPY --from=builder /workspace/services/order-service/order_service ./order_service
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "order_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
