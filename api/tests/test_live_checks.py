import io

import pytest
from fastapi import HTTPException
from PIL import Image

from locomission import live_checks
from locomission.vision import Verdict


def test_synthetic_photo_contains_no_exif_and_valid_jpeg():
    data = live_checks._synthetic_image()
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (320, 240)
        assert not image.getexif()


def test_clerk_smoke_fails_without_live_credentials(monkeypatch):
    monkeypatch.delenv("CLERK_SESSION_JWT", raising=False)
    with pytest.raises(live_checks.SmokeBlocked):
        live_checks.verify_clerk_session()


def test_live_clerk_requires_signed_token_and_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("LOCOMISSION_DEV_MODE", "false")
    monkeypatch.setenv("CLERK_SESSION_JWT", "test.verified.jwt")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_test")
    monkeypatch.setenv("CLERK_JWT_KEY", "public-pem")
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "https://play.example.org")
    seen = []

    def dummy_auth(request):
        token = request.headers["authorization"].removeprefix("Bearer ")
        seen.append(token)
        if token == "invalid.fixture.token":
            raise HTTPException(401, "Rejected")
        return "user_fixture"

    monkeypatch.setattr(live_checks, "clerk_subject", dummy_auth)
    live_checks.verify_clerk_session()
    assert seen == ["test.verified.jwt", "invalid.fixture.token"]


def test_live_openai_dry_run_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(live_checks.SmokeBlocked):
        live_checks.verify_openai_vision()


def test_live_openai_uses_synthetic_not_user_image(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    def dummy_vision(data, title, instructions, criteria):
        assert data.startswith(b"\xff\xd8")
        assert "סינתטית" in title
        assert len(criteria) == 2
        return Verdict(False, "אין התאמה")
    monkeypatch.setattr(live_checks, "grade_photo", dummy_vision)
    live_checks.verify_openai_vision()
