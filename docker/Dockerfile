# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ── build stage: install dependencies ──────────────────────────────────────
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY apps config ingestion pipelines scripts ./
RUN pip install --upgrade pip \
    && pip install uv \
    && uv pip install --system --no-cache .

# ── runtime stage ──────────────────────────────────────────────────────────
FROM base AS runtime

RUN groupadd --system app && useradd --system --gid app app

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

RUN mkdir -p /app/warehouse && chown -R app:app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "apps.main:app", "--host", "0.0.0.0", "--port", "8000"]
