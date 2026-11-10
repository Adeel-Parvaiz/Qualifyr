"""Gmail OAuth2 (XOAUTH2) without an App Password.

One-time setup (free): create an OAuth client of type "Desktop app" in Google Cloud
Console, enable the Gmail API, then run `gtm outreach gmail-auth` and paste the client
id/secret. It prints a refresh token; store the three values as
GTM_GMAIL_CLIENT_ID / GTM_GMAIL_CLIENT_SECRET / GTM_GMAIL_REFRESH_TOKEN.

Access tokens are minted from the refresh token on demand and cached in memory."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://mail.google.com/"
REDIRECT_OOB = "urn:ietf:wg:oauth:2.0:oob"


def credentials_from_env() -> tuple[str, str, str] | None:
    cid = os.environ.get("GTM_GMAIL_CLIENT_ID")
    secret = os.environ.get("GTM_GMAIL_CLIENT_SECRET")
    refresh = os.environ.get("GTM_GMAIL_REFRESH_TOKEN")
    if cid and secret and refresh:
        return cid, secret, refresh
    return None


class AccessTokenProvider:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._http = http or httpx.Client(timeout=20)
        self._token: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        resp = self._http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        })
        if resp.status_code != 200:
            raise RuntimeError(f"gmail oauth: token refresh failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + float(data.get("expires_in", 3600))
        return self._token


def xoauth2_string(user: str, access_token: str) -> str:
    """SASL XOAUTH2 initial client response (RFC 7628 style used by Google)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def xoauth2_b64(user: str, access_token: str) -> str:
    return base64.b64encode(xoauth2_string(user, access_token).encode()).decode()


def authorization_url(client_id: str) -> str:
    params = {
        "client_id": client_id, "redirect_uri": REDIRECT_OOB, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT_OOB,
    }, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"gmail oauth: code exchange failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def interactive_setup() -> None:
    """Console flow that ends with the refresh token printed once."""
    client_id = input("OAuth client id: ").strip()
    client_secret = input("OAuth client secret: ").strip()
    print("\nOpen this URL in a browser, sign in to the sending Gmail account and paste the code:\n")
    print(authorization_url(client_id))
    code = input("\nAuthorization code: ").strip()
    data = exchange_code(client_id, client_secret, code)
    refresh = data.get("refresh_token")
    if not refresh:
        raise SystemExit("no refresh token returned; revoke the app at myaccount.google.com/permissions and retry")
    print("\nAdd these as repository secrets / environment variables:")
    print(f"  GTM_GMAIL_CLIENT_ID={client_id}")
    print(f"  GTM_GMAIL_CLIENT_SECRET={client_secret}")
    print(f"  GTM_GMAIL_REFRESH_TOKEN={refresh}")
    print("\nGTM_SMTP_USER stays the Gmail address; GTM_SMTP_PASSWORD can be removed.")
    print(json.dumps({"scope": data.get("scope"), "expires_in": data.get("expires_in")}))
