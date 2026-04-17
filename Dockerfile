FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev 2>/dev/null || uv sync --no-install-project --no-dev

COPY src ./src
RUN uv sync --no-dev

# Run as a fixed non-root UID. If the host's ./data volume is owned by a
# different UID, override at runtime with `user: "<uid>:<gid>"` in compose.
RUN groupadd --gid 10001 spacebee \
 && useradd --uid 10001 --gid 10001 --no-create-home spacebee \
 && mkdir -p /data/passthrough \
 && chown -R spacebee:spacebee /app /data
USER spacebee

ENV PATH="/app/.venv/bin:$PATH" \
    PASSTHROUGH_ROOT=/data/passthrough \
    LOG_LEVEL=INFO

EXPOSE 8080

CMD ["uvicorn", "spacebee.main:app", "--host", "0.0.0.0", "--port", "8080"]
