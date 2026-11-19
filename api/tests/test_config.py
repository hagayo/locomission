"""Deployment configuration is testable without real credentials."""
import pytest
from locomission.config import load_runtime


def local_config():
    return {
        "DATABASE_URL": "postgresql://unused:unused@localhost:5432/locomission_test",
        "REDIS_URL": "redis://localhost:6379/0",
        "LOCOMISSION_ENV": "local",
    }


def staging_config():
    return {
        **local_config(),
        "LOCOMISSION_ENV": "staging",
        "CLERK_SECRET_KEY": "sk_test_example_not_a_real_secret",
        "CLERK_JWT_KEY": "-----BEGIN PUBLIC KEY-----\\nNOT_A_KEY\\n-----END PUBLIC KEY-----",
        "CLERK_AUTHORIZED_PARTIES": "https://play.example.com",
        "OPENAI_API_KEY": "sk-example-not-real",
    }


def test_local_config():
    actual = load_runtime(local_config())
    assert actual.environment == "local"
    assert actual.vision_concurrency == 4


def test_staging_config():
    assert load_runtime(staging_config()).environment == "staging"


@pytest.mark.parametrize("changed", [
    {"LOCOMISSION_DEV_MODE": "true"},
    {"CLERK_SECRET_KEY": "sk_test_REPLACE_WITH_SECRET"},
    {"CLERK_JWT_KEY": "REPLACE"},
    {"CLERK_AUTHORIZED_PARTIES": "http://example.com"},
    {"CLERK_AUTHORIZED_PARTIES": "https://*.example.com"},
    {"CLERK_AUTHORIZED_PARTIES": "https://foo.example/unsafe/path"},
    {"OPENAI_API_KEY": "REPLACE"},
    {"LOCOMISSION_MAX_VISION_CONCURRENCY": "33"},
])
def test_staging_fails_closed(changed):
    with pytest.raises(RuntimeError):
        load_runtime({**staging_config(), **changed})


def test_cloud_run_never_uses_local_or_dev_bypass():
    with pytest.raises(RuntimeError):
        load_runtime({**local_config(), "K_SERVICE": "locomission"})
    with pytest.raises(RuntimeError):
        load_runtime({**staging_config(), "K_SERVICE": "locomission",
                      "LOCOMISSION_DEV_MODE": "true"})


def test_invalid_connection_scheme_rejected():
    with pytest.raises(RuntimeError):
        load_runtime({**local_config(), "REDIS_URL": "http://localhost"})
    with pytest.raises(RuntimeError):
        load_runtime({**local_config(), "DATABASE_URL": "sqlite:///db"})
