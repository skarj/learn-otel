# Build context is the repo root: docker build -f services/load-generator/Dockerfile .
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv==0.6.6
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/load-generator ./services/load-generator
RUN uv sync --frozen --package load-generator --no-dev

FROM python:3.12-slim
RUN useradd -u 1000 -m appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/libs/common ./libs/common
COPY --from=builder /app/services/load-generator/load_generator ./load_generator
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/libs/common:$PYTHONPATH"
USER appuser
CMD ["python", "-m", "load_generator.main"]
