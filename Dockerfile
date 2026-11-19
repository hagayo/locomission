# Build from repository root: docker build -t locomission-api .
FROM python:3.12-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build/api
COPY api/pyproject.toml ./
COPY api/src/ ./src/
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
WORKDIR /app
COPY --from=build /wheels/ /tmp/wheels/
RUN python -m pip install --no-index --find-links=/tmp/wheels locomission-api \
    && rm -rf /tmp/wheels
COPY db/ /app/db/
# Numeric unprivileged user; image never contains .env files or API keys.
USER 10001:10001
EXPOSE 8080
# One worker per instance keeps memory and model concurrency bounded on Render.
CMD ["sh", "-c", "exec uvicorn locomission.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-access-log"]
