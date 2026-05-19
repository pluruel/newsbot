FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv --quiet

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY newsparser/ ./newsparser/
COPY sources.md ./
