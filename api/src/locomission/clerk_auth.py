"""Clerk bearer-JWT authentication. Never trust browser-supplied user IDs."""
from __future__ import annotations

import os
from fastapi import HTTPException, Request


def is_dev_mode() -> bool:
    return os.getenv("LOCOMISSION_DEV_MODE", "").lower() == "true"


def clerk_subject(request: Request) -> str:
    """Return Clerk user ID from a cryptographically verified session token.

    Production requires a PEM signing *public* key, Backend API secret and
    allowed first-party origins. A placeholder is not a valid configuration.
    Require Authorization bearer explicitly; do not rely on ambient cookies.
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not auth.removeprefix("Bearer ").strip():
        raise HTTPException(401, "נדרשת התחברות באמצעות Clerk")
    secret = os.getenv("CLERK_SECRET_KEY", "")
    public = os.getenv("CLERK_JWT_KEY", "").replace("\\n", "\n")
    parties = [
        s.strip() for s in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
        if s.strip()
    ]
    if (
        not secret.startswith(("sk_test_", "sk_live_"))
        or "REPLACE" in secret
        or "BEGIN PUBLIC KEY" not in public
        or "REPLACE" in public
        or not parties
        or any("REPLACE" in x for x in parties)
    ):
        raise HTTPException(503, "Clerk אינו מוגדר בשרת")
    from clerk_backend_api import authenticate_request, AuthenticateRequestOptions
    try:
        verified = authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=secret,
                jwt_key=public,
                authorized_parties=parties,
                accepts_token=["session_token"],
            ),
        )
        subject = verified.payload.get("sub") if verified.is_signed_in else None
    except Exception:
        # Never leak tokens, exceptions, private key material, or claims.
        subject = None
    if not isinstance(subject, str) or not subject.startswith("user_") or len(subject) > 128:
        raise HTTPException(401, "ההתחברות אינה תקפה")
    return subject
