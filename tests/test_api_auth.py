"""Every route except /health must refuse an unauthenticated request.

This is the regression guard for a real exposure: the API shipped to Vercel with no auth
at all, so leads, contact emails and exports were readable with a bare curl and
`POST /campaigns/{id}/run` would spend crawl credit for a stranger.

Tokens here are signed with a throwaway EC key rather than mocked away, so the actual
PyJWT verification runs - an expired or re-signed token has to be rejected by the real
code path, not by a stub that agreed to say no.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from gtm_engine.api import auth

ISSUER_BASE = "https://test-project.supabase.co"
# File-backed: _campaign() resolves this from config/campaigns/*.yaml, so a 200 here proves
# the request passed auth without needing a database.
PROTECTED = "/campaigns/retail-isb-001"


@pytest.fixture
def signing_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def client(monkeypatch, signing_key):
    """The real app, with only the JWKS fetch replaced by the local public key."""
    monkeypatch.setenv("GTM_SUPABASE_URL", ISSUER_BASE)
    monkeypatch.delenv("GTM_AUTH_DISABLED", raising=False)

    class _Key:
        key = signing_key.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(auth, "_jwk_client", _Client())

    from gtm_engine.api.main import app

    return TestClient(app, raise_server_exceptions=False)


def make_token(signing_key, *, issuer=f"{ISSUER_BASE}/auth/v1", audience="authenticated",
               expires_in=3600, key=None):
    now = int(time.time())
    return jwt.encode(
        {"sub": "operator", "aud": audience, "iss": issuer,
         "iat": now, "exp": now + expires_in},
        key or signing_key,
        algorithm="ES256",
    )


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


def test_missing_header_is_rejected(client):
    r = client.get(PROTECTED)
    assert r.status_code == 401
    # Tells the client this is "sign in", not "forbidden forever".
    assert r.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.parametrize("header", [
    {"Authorization": "token abc"},          # wrong scheme
    {"Authorization": "Bearer"},             # scheme with no token
    {"Authorization": "Bearer "},
    {"Authorization": "Bearer not-a-jwt"},
    {"Authorization": ""},
])
def test_malformed_credentials_are_rejected(client, header):
    assert client.get(PROTECTED, headers=header).status_code == 401


def test_valid_token_is_accepted(client, signing_key):
    r = client.get(PROTECTED, headers=bearer(make_token(signing_key)))
    assert r.status_code == 200
    assert r.json()["campaign_id"] == "retail-isb-001"


def test_expired_token_is_rejected(client, signing_key):
    token = make_token(signing_key, expires_in=-60)
    assert client.get(PROTECTED, headers=bearer(token)).status_code == 401


def test_token_signed_by_another_key_is_rejected(client, signing_key):
    """The whole point of verifying: a well-formed token from elsewhere is still a forgery."""
    attacker = ec.generate_private_key(ec.SECP256R1())
    token = make_token(signing_key, key=attacker)
    assert client.get(PROTECTED, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("claims", [
    {"issuer": "https://someone-elses-project.supabase.co/auth/v1"},
    {"audience": "anon"},
])
def test_tokens_for_another_project_or_audience_are_rejected(client, signing_key, claims):
    """A token minted by a different Supabase project must not unlock this one."""
    assert client.get(PROTECTED, headers=bearer(make_token(signing_key, **claims))).status_code == 401


def test_rejection_does_not_explain_itself(client, signing_key):
    """Don't tell a forger which part to fix next; the reason goes to the log instead."""
    body = client.get(PROTECTED, headers=bearer(make_token(signing_key, expires_in=-60))).json()
    assert body["detail"] == "token expired"
    assert "signature" not in str(body).lower()


def test_unset_project_url_fails_closed(client, monkeypatch, signing_key):
    """An unset variable must never reopen the API - that is the bug this module fixes."""
    monkeypatch.delenv("GTM_SUPABASE_URL", raising=False)
    r = client.get(PROTECTED, headers=bearer(make_token(signing_key)))
    assert r.status_code == 500
    assert "GTM_SUPABASE_URL" in r.json()["detail"]


def test_auth_disabled_is_opt_in_only(client, monkeypatch):
    monkeypatch.setenv("GTM_AUTH_DISABLED", "1")
    assert client.get(PROTECTED).status_code == 200
    # Anything other than an explicit yes leaves auth on, so a typo cannot open the door.
    for value in ("0", "false", "", "no", "TRUE-ish"):
        monkeypatch.setenv("GTM_AUTH_DISABLED", value)
        assert client.get(PROTECTED).status_code == 401, f"{value!r} disabled auth"


def test_docs_do_not_exist_in_production(client):
    """They publish all 35 routes, and FastAPI registers them outside the app dependencies -
    so they were still public on the first attempt. main.py now omits them entirely unless
    auth is explicitly disabled, which makes them absent rather than merely guarded."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path
