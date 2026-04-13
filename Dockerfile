FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev 2>/dev/null || uv sync --no-install-project --no-dev

COPY src ./src
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PASSTHROUGH_ROOT=/data/passthrough \
    LOG_LEVEL=INFO

EXPOSE 8080

CMD ["uvicorn", "waggle.main:app", "--host", "0.0.0.0", "--port", "8080"]
