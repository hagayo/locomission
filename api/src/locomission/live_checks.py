"""Opt-in staging smoke checks. Never print tokens, identity or image bytes.

Clerk check requires a freshly issued REAL Clerk session JWT. OpenAI check
calls the existing paid vision adapter once with a synthetic JPEG only.
Neither runs in the default test suite or GitHub Actions pipeline.
"""
from __future__ import annotations

import argparse
import io
import os

from fastapi import HTTPException
from PIL import Image, ImageDraw
from starlette.requests import Request

from .clerk_auth import clerk_subject
from .vision import Verdict, grade_photo


class SmokeBlocked(RuntimeError):
    pass


def _clerk_request(jwt: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/me",
        "scheme": "https",
        "server": ("localhost", 443),
        "headers": [(b"authorization", ("Bearer " + jwt).encode("ascii"))],
    })


def verify_clerk_session() -> None:
    """Locally validate one live JWT and reject a deliberately malformed one."""
    if os.getenv("LOCOMISSION_DEV_MODE", "").lower() == "true":
        raise SmokeBlocked("Disable development authentication before live check")
    if not all(os.getenv(k) for k in (
        "CLERK_SESSION_JWT", "CLERK_SECRET_KEY",
        "CLERK_JWT_KEY", "CLERK_AUTHORIZED_PARTIES",
    )):
        raise SmokeBlocked("Live Clerk credentials and session JWT not configured")
    jwt = os.environ["CLERK_SESSION_JWT"]
    try:
        subject = clerk_subject(_clerk_request(jwt))
    except HTTPException as exc:
        raise SmokeBlocked("Live Clerk JWT verification failed") from exc
    if not subject.startswith("user_"):
        raise SmokeBlocked("Invalid verified user subject")
    try:
        clerk_subject(_clerk_request("invalid.fixture.token"))
    except HTTPException as exc:
        if exc.status_code == 401:
            return
    raise SmokeBlocked("Invalid Clerk bearer was not rejected")


def _synthetic_image() -> bytes:
    """No photo of a real person, place or player can enter the paid smoke call."""
    image = Image.new("RGB", (320, 240), (242, 242, 242))
    pen = ImageDraw.Draw(image)
    pen.rectangle((28, 36, 180, 190), fill=(35, 180, 60))
    pen.ellipse((208, 70, 288, 150), fill=(220, 45, 45))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=75)
    return buffer.getvalue()


def verify_openai_vision() -> None:
    """One explicitly authorized, potentially billable OpenAI model call."""
    if not os.getenv("OPENAI_API_KEY"):
        raise SmokeBlocked("OPENAI_API_KEY is not configured")
    response = grade_photo(
        _synthetic_image(),
        "בדיקת מנוע תמונות סינתטית",
        "זהה צורות בלבד, אין כאן צילום של שחקן או אתר היסטורי",
        ["בתמונה מלבן ירוק גדול", "בתמונה עיגול אדום ברור"],
    )
    if (not isinstance(response, Verdict)
            or not isinstance(response.accepted, bool)
            or not response.reason_he.strip()):
        raise SmokeBlocked("Model did not return valid structured verdict")


def main() -> int:
    cli = argparse.ArgumentParser(description="Explicit live-provider smoke test")
    actions = cli.add_subparsers(dest="service", required=True)
    clerk = actions.add_parser("clerk")
    clerk.add_argument("--confirm-live", action="store_true")
    openai = actions.add_parser("openai")
    openai.add_argument("--confirm-paid", action="store_true")
    args = cli.parse_args()
    if ((args.service == "clerk" and not args.confirm_live) or
            (args.service == "openai" and not args.confirm_paid)):
        print("BLOCKED: explicit live/provider-cost confirmation required")
        return 2
    try:
        if args.service == "clerk":
            verify_clerk_session()
        else:
            verify_openai_vision()
    except Exception:
        # Do not print token, provider exception or request payload contents.
        print("FAILED:", "missing configuration or live check error")
        return 1
    print("PASSED:", args.service, "smoke check (no real player images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
