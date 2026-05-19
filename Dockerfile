FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY newsparser/ ./newsparser/
COPY sources.md ./

RUN pip install --no-cache-dir --no-deps -e .
