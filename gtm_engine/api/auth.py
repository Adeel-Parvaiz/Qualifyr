"""Bearer-token auth for the API, backed by Firebase Authentication."""

from __future__ import annotations

import json
import logging
import os

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from firebase_admin.exceptions import FirebaseError
from fastapi import HTTPException, Request
from google.auth.exceptions import DefaultCredentialsError

log = logging.getLogger(__name__)

PUBLIC_PATHS = frozenset({
    "/health", "/pricing/tiers", "/webhooks/paypal",
    "/paypal/orders/return", "/paypal/orders/status",
})


def _firebase_auth() -> firebase_auth.Client:
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
    if not project_id:
        raise HTTPException(
            500,
            "FIREBASE_PROJECT_ID is not set, so no request can be authenticated. "
            "Refusing to serve rather than fail open.",
        )

    try:
        app = firebase_admin.get_app()
    except ValueError:
        service_account = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        try:
            if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
                credential = None
            elif service_account:
                credential = credentials.Certificate(json.loads(service_account))
            else:
                credential = credentials.ApplicationDefault()
            app = firebase_admin.initialize_app(
                credential,
                {"projectId": project_id},
            )
        except (DefaultCredentialsError, json.JSONDecodeError, ValueError) as exc:
            log.error("Firebase Admin initialization failed: %s", exc)
            raise HTTPException(503, "Firebase Admin credentials are unavailable") from exc

    if app.project_id != project_id:
        raise HTTPException(500, "Firebase Admin project does not match FIREBASE_PROJECT_ID")
    return firebase_auth.Client(app)


def auth_disabled() -> bool:
    """Explicit local-dev escape hatch; missing or misspelled values leave auth enabled."""
    return os.environ.get("GTM_AUTH_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def verify_request(request: Request) -> dict | None:
    """Reject requests without a valid Firebase ID token."""
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return None

    if auth_disabled():
        log.warning("GTM_AUTH_DISABLED is set: serving %s unauthenticated", request.url.path)
        return None

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})

    auth_client = _firebase_auth()
    try:
        payload = auth_client.verify_id_token(token)
    except firebase_auth.ExpiredIdTokenError:
        raise HTTPException(401, "token expired", headers={"WWW-Authenticate": "Bearer"})
    except firebase_auth.InvalidIdTokenError as exc:
        log.info("rejected token on %s: %s", request.url.path, exc)
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    except FirebaseError as exc:
        log.error("Firebase token verification failed: %s", exc)
        raise HTTPException(503, "Firebase token verification is unavailable") from exc

    if not payload.get("uid") and not payload.get("sub"):
        log.info("rejected token on %s: no user id claim", request.url.path)
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})

    request.state.user = payload
    return payload


def current_user_id(request: Request) -> str | None:
    """Return the verified Firebase UID, or None for an explicitly bypassed request."""
    user = getattr(request.state, "user", None)
    return (user.get("uid") or user.get("sub")) if isinstance(user, dict) else None


def current_user_email(request: Request) -> str | None:
    """Return the verified email claim when present."""
    user = getattr(request.state, "user", None)
    return user.get("email") if isinstance(user, dict) else None
