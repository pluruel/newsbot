FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv --quiet

# Install the Claude Code CLI (runner.py invokes `claude` as a subprocess).
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY newsparser/ ./newsparser/
COPY sources.md ./
