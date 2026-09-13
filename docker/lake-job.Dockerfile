# Lake pipeline image: built by CI, executed by Hugging Face Jobs.
# Nothing here runs on the collector VPS.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --extra dev --extra stream --extra turso --no-install-project
COPY . .
RUN uv sync --extra dev --extra stream --extra turso

ENTRYPOINT ["bash", "scripts/run_lake.sh"]
