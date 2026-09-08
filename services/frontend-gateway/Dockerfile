# Build context is the repo root: docker build -f services/frontend-gateway/Dockerfile .
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv==0.6.6
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/frontend-gateway ./services/frontend-gateway
RUN uv sync --frozen --package frontend-gateway --no-dev

FROM python:3.12-slim
RUN useradd -u 1000 -m appuser
WORKDIR /app
COPY --from=builder /workspace/.venv /app/.venv
COPY --from=builder /workspace/services/frontend-gateway/frontend_gateway ./frontend_gateway
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "frontend_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
