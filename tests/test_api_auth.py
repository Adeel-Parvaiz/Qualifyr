"""Every route except the public allow-list must reject unauthenticated requests."""

import pytest
from firebase_admin import auth as firebase_auth
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gtm_engine.api import auth

PROTECTED = "/campaigns/retail-isb-001"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "qualifyr-test")
    monkeypatch.delenv("GTM_AUTH_DISABLED", raising=False)

    class _Verifier:
        def verify_id_token(self, token):
            if token == "valid":
                return {"uid": "operator", "email": "operator@example.com"}
            if token == "expired":
                raise firebase_auth.ExpiredIdTokenError("expired", None)
            raise firebase_auth.InvalidIdTokenError("invalid")

    monkeypatch.setattr(auth, "_firebase_auth", lambda: _Verifier())

    from gtm_engine.api.main import app

    return TestClient(app, raise_server_exceptions=False)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


def test_missing_header_is_rejected(client):
    response = client.get(PROTECTED)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.parametrize("header", [
    {"Authorization": "token abc"},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic abc"},
    {"Authorization": ""},
])
def test_malformed_credentials_are_rejected(client, header):
    assert client.get(PROTECTED, headers=header).status_code == 401


def test_valid_token_is_accepted(client):
    response = client.get(PROTECTED, headers=bearer("valid"))
    assert response.status_code == 200
    assert response.json()["campaign_id"] == "retail-isb-001"


def test_expired_token_is_rejected(client):
    response = client.get(PROTECTED, headers=bearer("expired"))
    assert response.status_code == 401
    assert response.json()["detail"] == "token expired"


def test_invalid_token_is_rejected_without_claim_details(client):
    response = client.get(PROTECTED, headers=bearer("invalid"))
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"


def test_unset_project_id_fails_closed(monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    with pytest.raises(HTTPException, match="FIREBASE_PROJECT_ID"):
        auth._firebase_auth()


def test_auth_disabled_is_opt_in_only(client, monkeypatch):
    monkeypatch.setenv("GTM_AUTH_DISABLED", "1")
    assert client.get(PROTECTED).status_code == 200
    for value in ("0", "false", "", "no", "TRUE-ish"):
        monkeypatch.setenv("GTM_AUTH_DISABLED", value)
        assert client.get(PROTECTED).status_code == 401, f"{value!r} disabled auth"


def test_route_level_unauthorized_is_not_an_auth_challenge(client, monkeypatch):
    monkeypatch.setenv("GTM_AUTH_DISABLED", "1")
    response = client.get("/settings/api-keys")
    assert response.status_code == 401
    assert response.json()["detail"] == "sign in to manage API keys"
    assert "WWW-Authenticate" not in response.headers


def test_docs_do_not_exist_in_production(client):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path
