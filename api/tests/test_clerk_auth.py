"""Clerk verification boundaries; never use real Clerk keys in tests."""
from types import SimpleNamespace
import pytest
from starlette.requests import Request
from fastapi import HTTPException
from locomission.clerk_auth import clerk_subject


def request(auth="Bearer pretend.jwt.token"):
    return Request({"type": "http", "headers": [(b"authorization", auth.encode())]})


def test_no_bearer_rejected():
    with pytest.raises(HTTPException) as error:
        clerk_subject(request(""))
    assert error.value.status_code == 401


def test_unconfigured_clerk_fails_closed(monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_JWT_KEY", raising=False)
    with pytest.raises(HTTPException) as error:
        clerk_subject(request())
    assert error.value.status_code == 503


def test_signed_identity_and_explicit_origin(monkeypatch):
    import clerk_backend_api
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_local_test_key")
    monkeypatch.setenv("CLERK_JWT_KEY",
                       "-----BEGIN PUBLIC KEY-----\\nFAKE-TEST-KEY\\n-----END PUBLIC KEY-----")
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES",
                       "http://localhost:5173,https://test.example")
    def verified(req, options):
        assert options.accepts_token == ["session_token"]
        assert options.authorized_parties == [
            "http://localhost:5173", "https://test.example"
        ]
        return SimpleNamespace(is_signed_in=True,payload={"sub":"user_test_123"})
    monkeypatch.setattr(clerk_backend_api, "authenticate_request", verified)
    assert clerk_subject(request()) == "user_test_123"

    monkeypatch.setattr(clerk_backend_api,"authenticate_request",
                        lambda req,opts: SimpleNamespace(
                            is_signed_in=False,payload={}
                        ))
    with pytest.raises(HTTPException) as error:
        clerk_subject(request())
    assert error.value.status_code == 401
