FROM python:3.12-slim

RUN pip install uv --quiet

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY newsparser/ ./newsparser/
COPY sources.md ./
