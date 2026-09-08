# Build context is the repo root: docker build -f services/catalog-service/Dockerfile .
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv==0.6.6
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/catalog-service ./services/catalog-service
RUN uv sync --frozen --package catalog-service --no-dev

FROM python:3.12-slim
RUN useradd -u 1000 -m appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/libs/common ./libs/common
COPY --from=builder /app/services/catalog-service/catalog_service ./catalog_service
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/libs/common:$PYTHONPATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "catalog_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
