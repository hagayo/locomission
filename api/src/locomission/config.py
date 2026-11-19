"""Deployment configuration guardrails; never print secret values."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    database_url: str
    redis_url: str
    vision_concurrency: int


def _present(value: str) -> bool:
    return bool(value and "REPLACE" not in value and "YOUR_" not in value)


def load_runtime(source: Mapping[str, str] | None = None) -> RuntimeConfig:
    env = os.environ if source is None else source
    mode = env.get("LOCOMISSION_ENV", "local").strip().lower()
    if mode not in ("local", "staging", "production"):
        raise RuntimeError("Invalid LOCOMISSION_ENV; expected local, staging or production")

    is_cloud_run = bool(env.get("K_SERVICE"))
    if is_cloud_run and mode == "local":
        raise RuntimeError("LOCOMISSION_ENV must be staging/production on Cloud Run")
    dev_bypass = env.get("LOCOMISSION_DEV_MODE", "false").strip().lower() == "true"
    if dev_bypass and (mode != "local" or is_cloud_run):
        raise RuntimeError("Development auth bypass is forbidden outside local mode")

    database_url = env.get("DATABASE_URL", "")
    redis_url = env.get("REDIS_URL", "")
    if urlsplit(database_url).scheme not in ("postgres", "postgresql"):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URI")
    if urlsplit(redis_url).scheme not in ("redis", "rediss"):
        raise RuntimeError("REDIS_URL must be a Redis connection URI")

    try:
        concurrency = int(env.get("LOCOMISSION_MAX_VISION_CONCURRENCY", "4"))
    except ValueError as exc:
        raise RuntimeError("Invalid vision concurrency configuration") from exc
    if not 1 <= concurrency <= 32:
        raise RuntimeError("Vision concurrency must be between 1 and 32")

    if mode in ("staging", "production"):
        secret = env.get("CLERK_SECRET_KEY", "")
        key = env.get("CLERK_JWT_KEY", "").replace("\\n", "\n")
        parties = env.get("CLERK_AUTHORIZED_PARTIES", "")
        api_key = env.get("OPENAI_API_KEY", "")
        if not _present(secret) or not secret.startswith(("sk_test_", "sk_live_")):
            raise RuntimeError("CLERK_SECRET_KEY is missing or a placeholder")
        if not _present(key) or "BEGIN PUBLIC KEY" not in key:
            raise RuntimeError("CLERK_JWT_KEY is missing or a placeholder")
        if not _present(api_key):
            raise RuntimeError("OPENAI_API_KEY is missing or a placeholder")
        allowed = [value.strip() for value in parties.split(",") if value.strip()]
        if not allowed:
            raise RuntimeError("CLERK_AUTHORIZED_PARTIES must list HTTPS origins")
        for origin in allowed:
            parts = urlsplit(origin)
            if (parts.scheme != "https" or not parts.hostname
                    or parts.username or parts.password or parts.query
                    or parts.fragment or parts.path not in ("", "/")
                    or "*" in origin):
                raise RuntimeError("Only explicit HTTPS frontend origins are allowed")

    return RuntimeConfig(mode, database_url, redis_url, concurrency)
