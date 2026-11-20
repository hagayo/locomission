# Build from repository root: docker build -t locomission-api .
# Python dependencies are resolved exclusively from api/uv.lock using uv.
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY api/pyproject.toml api/uv.lock api/.python-version ./
COPY api/src/ ./src/
# Package into /app/.venv so virtualenv shebangs remain valid in final image.
# Exclude dev dependencies and editable source-code paths from production.
RUN uv sync --locked --no-dev --no-editable --compile-bytecode

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=build /app/.venv/ /app/.venv/
COPY db/ /app/db/
# Numeric unprivileged user; image never contains .env files or API keys.
USER 10001:10001
EXPOSE 8080
# One worker per instance keeps memory and model concurrency bounded on Render.
CMD ["sh", "-c", "exec uvicorn locomission.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-access-log"]
