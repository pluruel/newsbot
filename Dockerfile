FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY newsparser/ ./newsparser/
COPY sources.md ./

RUN pip install --no-cache-dir .
