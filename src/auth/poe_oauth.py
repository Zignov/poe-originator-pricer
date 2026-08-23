"""Path of Exile OAuth 2.0 authorization-code flow with PKCE.

STATUS: unverified scaffolding.

GGG's OAuth service requires a client registered by hand with them; there is no
self-service registration, so this flow could not be exercised end to end while
building the app. The endpoint paths and parameter names below follow GGG's
published developer documentation, and the code is structured so that supplying a
real client_id is the only change needed. Nothing here attempts to work around
the absence of a client.

Until a client exists, use src/stash/official_api.py:SessionStashSource or one of
the import adapters. See docs/data-access.md.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests

AUTHORIZE_URL = "https://www.pathofexile.com/oauth/authorize"
TOKEN_URL = "https://www.pathofexile.com/oauth/token"

#: Scope needed to read private stash tabs.
STASH_SCOPE = "account:stashes"
PROFILE_SCOPE = "account:profile"
DEFAULT_SCOPES = f"{PROFILE_SCOPE} {STASH_SCOPE}"


class OAuthNotConfigured(RuntimeError):
    pass


class OAuthError(RuntimeError):
    pass


@dataclass
class Token:
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    refresh_token: str | None = None
    scope: str = ""

    @property
    def expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(seconds=60)

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> "Token":
        expires_in = payload.get("expires_in")
        return cls(
            access_token=payload["access_token"],
            token_type=payload.get("token_type", "Bearer"),
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                if expires_in
                else None
            ),
            refresh_token=payload.get("refresh_token"),
            scope=payload.get("scope", ""),
        )


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


class PoeOAuthClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str = "http://localhost:8721/callback",
        user_agent: str = "poe-originator-pricer/0.1",
        scopes: str = DEFAULT_SCOPES,
    ):
        self.client_id = client_id or os.getenv("POE_OAUTH_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("POE_OAUTH_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri
        self.user_agent = user_agent
        self.scopes = scopes

    def configured(self) -> tuple[bool, str]:
        if not self.client_id:
            return False, (
                "POE_OAUTH_CLIENT_ID is not set. GGG registers OAuth clients manually; "
                "there is no self-service signup."
            )
        return True, ""

    # ------------------------------------------------------------------
    def authorization_url(self, verifier: str | None = None) -> tuple[str, str, str]:
        """Return (url, state, code_verifier)."""
        ok, why = self.configured()
        if not ok:
            raise OAuthNotConfigured(why)
        verifier = verifier or _pkce_pair()[0]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": self.scopes,
            "state": state,
            "redirect_uri": self.redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}", state, verifier

    def exchange_code(self, code: str, verifier: str) -> Token:
        ok, why = self.configured()
        if not ok:
            raise OAuthNotConfigured(why)
        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "code_verifier": verifier,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        resp = requests.post(
            TOKEN_URL, data=data, headers={"User-Agent": self.user_agent}, timeout=30
        )
        if resp.status_code != 200:
            raise OAuthError(f"token exchange failed: HTTP {resp.status_code} {resp.text[:200]}")
        return Token.from_response(resp.json())

    def refresh(self, token: Token) -> Token:
        if not token.refresh_token:
            raise OAuthError("token has no refresh_token")
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        resp = requests.post(
            TOKEN_URL, data=data, headers={"User-Agent": self.user_agent}, timeout=30
        )
        if resp.status_code != 200:
            raise OAuthError(f"token refresh failed: HTTP {resp.status_code}")
        return Token.from_response(resp.json())

    # ------------------------------------------------------------------
    def run_local_flow(self, timeout: float = 300.0) -> Token:
        """Open the browser, catch the redirect on localhost, exchange the code."""
        url, state, verifier = self.authorization_url()
        captured: dict[str, str] = {}
        done = threading.Event()
        parsed = urllib.parse.urlparse(self.redirect_uri)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                captured.update({k: v[0] for k, v in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h3>You can close this tab and return to the app.</h3></body></html>"
                )
                done.set()

            def log_message(self, *_args):
                return

        server = HTTPServer((parsed.hostname or "localhost", parsed.port or 80), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            webbrowser.open(url)
            if not done.wait(timeout):
                raise OAuthError("timed out waiting for the OAuth redirect")
        finally:
            server.shutdown()

        if captured.get("state") != state:
            raise OAuthError("OAuth state mismatch; aborting")
        if "code" not in captured:
            raise OAuthError(f"no authorization code returned: {captured}")
        return self.exchange_code(captured["code"], verifier)
